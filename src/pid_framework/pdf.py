import hashlib
import io
import re
import threading
from contextlib import closing
from uuid import uuid4

import pypdfium2 as pdfium

from pid_framework.models import Document, EngineeringGraph, Equipment, Evidence, Location, Page

# PDFium is not thread-safe, including concurrent operations on different documents.
PDF_LOCK = threading.RLock()
PUMP_TAG = re.compile(
    r"(?<![A-Z0-9])(?:\d{1,4}-P[A-Z]{0,4}-?\d{3,10}"
    r"|P(?:U|MP)?-\d{2,8})(?:[A-Z](?:/[A-Z])?|[ \t]+[A-Z]/[A-Z])?(?![A-Z0-9/-])"
)
HOLD = re.compile(r"\bHOLD(?:[ \t]*\d+)?\b")
NAMED_EQUIPMENT = re.compile(
    r"\b(?:HYDRAULIC POWER UNIT|TOPSIDE UMBILICAL TERMINATION UNIT|"
    r"(?:[A-Z]+[ \t]+){0,3}(?:COMPRESSOR|VESSEL|SEPARATOR|HEAT EXCHANGER|"
    r"STORAGE TANK|REACTOR|BOILER|TURBINE|FILTER|DOSING PUMP))"
    r"(?:[ \t]*\([A-Z]{2,8}\))?"
)
OTHER_TAG = re.compile(
    r"(?<![A-Z0-9])\d{1,4}-(?!P[A-Z]*-?\d)[A-Z]{1,6}-?\d{3,10}"
    r"(?:[A-Z](?:/[A-Z])?|[ \t]+[A-Z]/[A-Z])?(?![A-Z0-9/-])"
)


def extract_document(content: bytes, filename: str, revision: str) -> Document:
    evidence: list[Evidence] = []
    equipment: list[Equipment] = []
    pages: list[Page] = []
    limitations = [
        "Text candidates include named packages and tags; "
        "they are not confirmed equipment symbols.",
        "Source tags may refer to off-sheet equipment rather than depicted symbols.",
        "No complete semantic symbol, junction, signal, or cross-sheet connectivity extraction.",
        "No project standards, datasheets, or specification completeness verification.",
    ]
    with PDF_LOCK, pdfium.PdfDocument(content) as pdf:
        if len(pdf) > 50:
            raise ValueError("Prototype accepts at most 50 pages per document")
        for index in range(len(pdf)):
            with closing(pdf[index]) as page:
                if page.get_rotation() != 0:
                    raise ValueError(
                        "Rotated pages are not supported in this prototype. "
                        "Normalize page rotation before importing."
                    )
                width, height = page.get_size()
                if width <= 0 or height <= 0:
                    raise ValueError("PDF page has invalid dimensions")
                with closing(page.get_textpage()) as textpage:
                    count = textpage.count_chars()
                    if count > 200_000:
                        raise ValueError("Page exceeds prototype text extraction limit")
                    characters = [textpage.get_text_range(i, 1) for i in range(count)]
                    # Preserve a mapping even when a PDF character expands to multiple characters.
                    text = "".join(characters)
                    mapping = [i for i, character in enumerate(characters) for _ in character]
                    pages.append(
                        Page(number=index + 1, width=width, height=height, text_characters=count)
                    )
                    if not text.strip():
                        limitations.append(
                            f"Page {index + 1} has no readable text layer; OCR is not implemented."
                        )
                    for pattern, prefix in (
                        (PUMP_TAG, "tag"),
                        (OTHER_TAG, "other-tag"),
                        (NAMED_EQUIPMENT, "named-equipment"),
                        (HOLD, "hold"),
                    ):
                        for match in pattern.finditer(text):
                            boxes = [
                                textpage.get_charbox(i)
                                for i in sorted(set(mapping[match.start() : match.end()]))
                            ]
                            left = max(0, min(b[0] for b in boxes) / width)
                            top = max(0, 1 - max(b[3] for b in boxes) / height)
                            right = min(1, max(b[2] for b in boxes) / width)
                            bottom = min(1, 1 - min(b[1] for b in boxes) / height)
                            if left >= right or top >= bottom:
                                raise ValueError("Matched text has an invalid PDF bounding box")
                            ev = Evidence(
                                id=f"{prefix}-{len(evidence) + 1}",
                                location=Location(page=index + 1, bbox=(left, top, right, bottom)),
                                text=match.group(),
                            )
                            evidence.append(ev)
                            if prefix != "hold":
                                equipment.append(
                                    Equipment(
                                        id=f"equipment-{len(equipment) + 1}",
                                        tag=match.group(),
                                        kind="pump" if prefix == "tag" else "equipment",
                                        evidence_ids=[ev.id],
                                    )
                                )
    if not pages:
        raise ValueError("PDF has no pages")
    return Document(
        id=str(uuid4()),
        filename=filename,
        revision=revision,
        sha256=hashlib.sha256(content).hexdigest(),
        pages=pages,
        evidence=evidence,
        graph=EngineeringGraph(equipment=equipment),
        limitations=limitations,
    )


def render_page(content: bytes, number: int, max_dimension: int = 2200) -> bytes:
    with PDF_LOCK, pdfium.PdfDocument(content) as pdf:
        if not 1 <= number <= len(pdf):
            raise ValueError("Page is outside document")
        with closing(pdf[number - 1]) as page:
            width, height = page.get_size()
            scale = min(2, max_dimension / max(width, height))
            bitmap = page.render(scale=scale)
            try:
                image = bitmap.to_pil()
                output = io.BytesIO()
                image.save(output, format="PNG")
                return output.getvalue()
            finally:
                bitmap.close()
