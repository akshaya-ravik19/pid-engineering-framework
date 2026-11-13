import hashlib
import io
from contextlib import closing
from uuid import uuid4

import cv2
import numpy as np
import pypdfium2 as pdfium
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject

from pid_framework.models import (
    BoundaryProposal,
    BoundaryRequest,
    Document,
    Equipment,
    Location,
    OriginalExtract,
    OriginalExtractRequest,
    RegionText,
)
from pid_framework.pdf import PDF_LOCK, render_page
from pid_framework.store import ConflictError

TRACE_WARNINGS = [
    "Geometric linework proposal, not semantic piping or instrument connectivity.",
    "Crossing lines may be joined; dashed lines, gaps and detached symbols may be missed.",
    "Nearby disconnected notes and specifications may fall outside the proposal.",
    "No off-sheet references are followed. Add relevant source regions explicitly.",
    "Engineer must adjust the boundary and verify all required specifications before export.",
]


def selected_equipment(document: Document, request: BoundaryRequest) -> Equipment:
    if request.expected_version != document.version:
        raise ConflictError("Document changed. Reload before extracting.")
    equipment = next((e for e in document.graph.equipment if e.id == request.equipment_id), None)
    if equipment is None:
        raise ValueError("Selected equipment does not exist")
    if not equipment.confirmed:
        raise ValueError(
            "Select and confirm the depicted equipment region, not an off-sheet tag reference."
        )
    return equipment


def propose_boundary(
    document: Document, source: bytes, request: BoundaryRequest
) -> BoundaryProposal:
    equipment = selected_equipment(document, request)
    evidence = [e for e in document.evidence if e.id in equipment.evidence_ids]
    seed = next(
        (e.location for e in evidence if e.origin == "engineer_selection"),
        evidence[0].location,
    )
    with Image.open(io.BytesIO(render_page(source, seed.page, max_dimension=3200))) as image:
        gray = np.asarray(image.convert("L"))
    height, width = gray.shape
    binary = cv2.threshold(gray, 190, 255, cv2.THRESH_BINARY_INV)[1]
    # Bridge only subpixel-sized rendering breaks; large gaps remain explicit uncertainties.
    ink = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((3, 3), dtype=np.uint8))
    horizontal = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN, np.ones((1, max(12, width // 150)), dtype=np.uint8)
    )
    vertical = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN, np.ones((max(12, height // 150), 1), dtype=np.uint8)
    )
    _, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    line_ids, counts = np.unique(labels[(horizontal | vertical) > 0], return_counts=True)
    structural_ids = {
        int(label) for label, count in zip(line_ids, counts) if label != 0 and count >= 12
    }
    x1, y1, x2, y2 = seed.bbox
    # Tags are often offset from symbols. A small search halo is a proposal, never a fact.
    left = max(0, int((x1 - 0.02) * width))
    top = max(0, int((y1 - 0.02) * height))
    right = min(width, max(left + 1, int((x2 + 0.02) * width)))
    bottom = min(height, max(top + 1, int((y2 + 0.02) * height)))
    nearby = set(np.unique(labels[top:bottom, left:right]).tolist()) & structural_ids
    if not nearby:
        raise ValueError(
            "No connected linework found near this source region. "
            "Select a region including the actual equipment symbol and its piping."
        )
    connected = [stats[label] for label in sorted(nearby)]
    padding_x, padding_y = 0.025, 0.025
    bounds = (
        max(0, min(x1, min(int(s[0]) for s in connected) / width) - padding_x),
        max(0, min(y1, min(int(s[1]) for s in connected) / height) - padding_y),
        min(1, max(x2, max(int(s[0] + s[2]) for s in connected) / width) + padding_x),
        min(1, max(y2, max(int(s[1] + s[3]) for s in connected) / height) + padding_y),
    )
    warnings = list(TRACE_WARNINGS)
    if (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]) > 0.65:
        warnings.append(
            "Proposal covers most of the sheet, possibly through a border or crossing. "
            "Inspect and narrow it before confirming."
        )
    return BoundaryProposal(
        document_version=document.version,
        region=Location(page=seed.page, bbox=bounds),
        connected_components=len(nearby),
        warnings=warnings,
    )


def pdf_coordinates(page: pdfium.PdfPage, location: Location) -> tuple[float, float, float, float]:
    if page.get_rotation() != 0:
        raise ValueError("Normalize rotated pages before extracting")
    left, bottom, right, top = page.get_bbox()
    width, height = right - left, top - bottom
    x1, y1, x2, y2 = location.bbox
    return (
        left + x1 * width,
        top - y2 * height,
        left + x2 * width,
        top - y1 * height,
    )


def create_extract(
    document: Document, source: bytes, request: OriginalExtractRequest
) -> OriginalExtract:
    equipment = selected_equipment(document, request)
    evidence = [e for e in document.evidence if e.id in equipment.evidence_ids]
    primary = request.regions[0]
    a, b, c, d = primary.bbox
    if not any(
        e.location.page == primary.page
        and a <= e.location.bbox[0]
        and b <= e.location.bbox[1]
        and c >= e.location.bbox[2]
        and d >= e.location.bbox[3]
        for e in evidence
    ):
        raise ValueError("Primary crop must contain the selected equipment's source region")
    text = []
    with PDF_LOCK, pdfium.PdfDocument(source) as pdf:
        for region in request.regions:
            if region.page > len(pdf):
                raise ValueError("Source region page is outside document")
            with closing(pdf[region.page - 1]) as page, closing(page.get_textpage()) as tp:
                coordinates = pdf_coordinates(page, region)
                text.append(RegionText(location=region, text=tp.get_text_bounded(*coordinates)))
    return OriginalExtract(
        id=str(uuid4()),
        document_id=document.id,
        document_version=document.version,
        source_sha256=document.sha256,
        revision=document.revision,
        equipment_id=equipment.id,
        equipment_tag=equipment.tag,
        regions=request.regions,
        extracted_text=text,
        warnings=[
            "Original PDF content is cropped, not redrawn or scaled. No specifications are added.",
            "All visible source information within each confirmed crop is retained; "
            "information outside these regions is not included.",
            "Engineer confirms boundaries and notes, "
            "not design approval or automatic completeness.",
            "Cropped-out PDF content may remain recoverable. This is not a redaction tool.",
            "Text extraction is supplementary; visual PDF content is the source of truth.",
        ],
    )


def render_original_pdf(extract: OriginalExtract, source: bytes) -> bytes:
    if hashlib.sha256(source).hexdigest() != extract.source_sha256:
        raise ValueError("Source PDF failed checksum validation")
    reader = PdfReader(io.BytesIO(source))
    writer = PdfWriter()
    with PDF_LOCK, pdfium.PdfDocument(source) as pdf:
        for region in extract.regions:
            if not 1 <= region.page <= len(pdf):
                raise ValueError("Source region page is outside document")
            with closing(pdf[region.page - 1]) as page:
                coordinates = pdf_coordinates(page, region)
            copied = writer.add_page(reader.pages[region.page - 1])
            # Retain original content streams, fonts, line styles and coordinates.
            copied.mediabox = RectangleObject(coordinates)
            copied.cropbox = RectangleObject(coordinates)
            copied.trimbox = RectangleObject(coordinates)
            copied.bleedbox = RectangleObject(coordinates)
            copied.artbox = RectangleObject(coordinates)
    writer.add_metadata(
        {
            "/Title": f"{extract.equipment_tag} - source P&ID extract",
            "/Subject": (
                f"Source {extract.document_id}; revision {extract.revision}; "
                f"SHA256 {extract.source_sha256}; engineer-confirmed crop, not design approval"
            ),
        }
    )
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()
