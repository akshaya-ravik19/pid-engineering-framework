import base64
import io
from html import escape
from textwrap import wrap
from uuid import uuid4

from PIL import Image
from reportlab.graphics.shapes import Circle, Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import (
    Image as ReportImage,
)
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from pid_framework.models import Document, Package, PackageRequest
from pid_framework.pdf import render_page

MISSING = (
    "Equipment datasheet and service",
    "Design and operating pressure / temperature",
    "Pump duty, flow, differential head and driver",
    "Nozzle schedule and connection ratings",
    "Line sizes, classes, materials and isolation arrangements",
    "Instrument index, control narrative and protective functions",
    "Applicable project legend, standards and cross-sheet references",
)


def build_package(document: Document, request: PackageRequest) -> Package:
    if request.expected_version != document.version:
        raise ValueError("Document changed. Reload before generating.")
    entities = {e.id: e for e in document.graph.equipment}
    if request.equipment_id not in entities:
        raise ValueError("Selected equipment does not exist")
    if not entities[request.equipment_id].confirmed:
        raise ValueError("Confirm the equipment's depicted location before generating")
    if set(request.boundary_ids) - entities.keys():
        raise ValueError("Unknown boundary equipment ID")
    selected = {request.equipment_id}
    pending = [request.equipment_id]
    while pending and request.include_connected:
        current = pending.pop()
        if current in request.boundary_ids:
            continue
        for edge in document.graph.connections:
            if not edge.confirmed or current not in (edge.source, edge.target):
                continue
            other = edge.target if current == edge.source else edge.source
            if not entities[other].confirmed:
                raise ValueError("Confirmed connection reaches unconfirmed equipment")
            if other not in selected:
                selected.add(other)
                pending.append(other)
    equipment = [e for e in document.graph.equipment if e.id in selected]
    connections = [
        edge
        for edge in document.graph.connections
        if edge.confirmed and edge.source in selected and edge.target in selected
    ]
    refs = {
        r
        for e in equipment
        for r in e.evidence_ids + [r for s in e.specifications for r in s.evidence_ids]
    } | {r for edge in connections for r in edge.evidence_ids}
    return Package(
        id=str(uuid4()),
        document_id=document.id,
        document_version=document.version,
        revision=document.revision,
        selected_equipment_id=request.equipment_id,
        equipment=equipment,
        connections=connections,
        evidence=[e for e in document.evidence if e.id in refs],
        missing_information=[
            f"VERIFY COMPLETENESS: {item}. Not established by this prototype." for item in MISSING
        ],
        proposals=[
            "PROPOSED REVIEW TASK: obtain the approved pump datasheet and reconcile the schedule.",
            "PROPOSED REVIEW TASK: assess isolation, drains, vents and protective functions "
            "against project requirements. No physical additions have been designed.",
        ]
        if request.include_proposals
        else [],
        limitations=[
            "DRAFT - NOT FOR CONSTRUCTION. Engineer confirmation is not design approval.",
            "Generic connectivity sketch, not a standards-compliant native CAD P&ID.",
            "Only engineer-confirmed entities and connections are drawn; no inferred piping.",
            "Specification values are engineer-entered claims linked to source selections.",
            "Source crops show context, not proof of completeness or package boundaries.",
        ],
    )


def positions(package: Package) -> dict[str, tuple[int, int]]:
    return {
        e.id: (140 + (i % 3) * 280, 80 + (i // 3) * 130) for i, e in enumerate(package.equipment)
    }


def source_crops(package: Package, source: bytes) -> list[tuple[int, bytes]]:
    crops = []
    for page in sorted({e.location.page for e in package.evidence}):
        boxes = [e.location.bbox for e in package.evidence if e.location.page == page]
        x1 = max(0, min(b[0] for b in boxes) - 0.06)
        y1 = max(0, min(b[1] for b in boxes) - 0.06)
        x2 = min(1, max(b[2] for b in boxes) + 0.06)
        y2 = min(1, max(b[3] for b in boxes) + 0.06)
        with Image.open(io.BytesIO(render_page(source, page))) as image:
            cropped = image.crop(
                (
                    int(x1 * image.width),
                    int(y1 * image.height),
                    max(int(x1 * image.width) + 1, int(x2 * image.width)),
                    max(int(y1 * image.height) + 1, int(y2 * image.height)),
                )
            )
            buffer = io.BytesIO()
            cropped.save(buffer, format="PNG")
            crops.append((page, buffer.getvalue()))
    return crops


def detail_lines(package: Package) -> list[str]:
    lines = [
        f"Source {package.document_id}; revision {package.revision}; "
        f"model version {package.document_version}",
        "SPECIFICATION SCHEDULE - SOURCE-LINKED ENGINEER ENTRIES",
    ]
    for equipment in package.equipment:
        lines.append(
            f"{equipment.tag} ({equipment.kind}); evidence {', '.join(equipment.evidence_ids)}"
        )
        if not equipment.specifications:
            lines.append("  No specifications entered. Values remain unknown.")
        for spec in equipment.specifications:
            lines.append(f"  {spec.name}: {spec.value} [evidence: {', '.join(spec.evidence_ids)}]")
    lines += ["CONNECTION SCHEDULE"]
    lines += [
        f"{c.source} -- {c.target}: {c.description} [evidence: {', '.join(c.evidence_ids)}]"
        for c in package.connections
    ] or ["No confirmed connections; no piping has been invented."]
    lines += ["INFORMATION CHECKLIST (NOT A COMPLETENESS CERTIFICATE)"]
    lines += package.missing_information + package.proposals + package.limitations
    lines += [
        f"EVIDENCE {e.id}: page {e.location.page}, bbox {e.location.bbox}, {e.origin}: {e.text}"
        for e in package.evidence
    ]
    return lines


def render_svg(package: Package, source: bytes) -> bytes:
    pos = positions(package)
    diagram_height = 200 + ((len(pos) - 1) // 3) * 130
    elements = [
        '<rect width="100%" height="100%" fill="white"/>',
        '<g font-family="sans-serif" fill="#172a3a">',
        '<text x="30" y="32" font-size="21">Equipment detail draft - NOT FOR CONSTRUCTION</text>',
        '<text x="30" y="58" font-size="13">'
        "Confirmed connectivity only; generic symbols, not an approved P&amp;ID.</text>",
    ]
    for edge in package.connections:
        x1, y1 = pos[edge.source]
        x2, y2 = pos[edge.target]
        elements.append(
            f'<line x1="{x1}" y1="{y1 + 70}" x2="{x2}" y2="{y2 + 70}" '
            'stroke="#334155" stroke-width="2"/>'
        )
    for equipment in package.equipment:
        x, y = pos[equipment.id]
        y += 70
        if equipment.kind == "pump":
            elements.append(f'<circle cx="{x}" cy="{y}" r="28" fill="white" stroke="#146c94"/>')
            elements.append(
                f'<path d="M {x - 12} {y + 13} L {x + 16} {y} L {x - 12} {y - 13} Z" '
                'fill="none" stroke="#146c94"/>'
            )
        else:
            elements.append(
                f'<rect x="{x - 48}" y="{y - 22}" width="96" height="44" '
                'fill="white" stroke="#146c94"/>'
            )
        elements.append(
            f'<text x="{x}" y="{y + 47}" text-anchor="middle" font-size="13">'
            f"{escape(equipment.tag)}</text>"
        )
    cursor = diagram_height + 50
    for line in detail_lines(package):
        for part in wrap(line, width=110, replace_whitespace=False) or [""]:
            elements.append(f'<text x="30" y="{cursor}" font-size="12">{escape(part)}</text>')
            cursor += 18
        cursor += 5
    for page, data in source_crops(package, source):
        elements.append(f'<text x="30" y="{cursor + 20}">Source context - page {page}</text>')
        cursor += 35
        with Image.open(io.BytesIO(data)) as image:
            height = min(650, int(840 * image.height / image.width))
        elements.append(
            f'<image x="30" y="{cursor}" width="840" height="{height}" '
            f'href="data:image/png;base64,{base64.b64encode(data).decode()}"/>'
        )
        cursor += height + 30
    elements.append("</g>")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="{cursor}" '
        f'viewBox="0 0 900 {cursor}">' + "".join(elements) + "</svg>"
    ).encode()


def render_pdf(package: Package, source: bytes) -> bytes:
    buffer = io.BytesIO()
    styles = getSampleStyleSheet()
    flow = [
        Paragraph("Equipment detail draft - NOT FOR CONSTRUCTION", styles["Title"]),
        Paragraph("Generic connectivity sketch; not an approved P&amp;ID.", styles["Normal"]),
        Spacer(1, 15),
    ]
    pos = positions(package)
    diagram_height = 190 + ((len(pos) - 1) // 3) * 130
    drawing = Drawing(850, diagram_height)
    for edge in package.connections:
        x1, y1 = pos[edge.source]
        x2, y2 = pos[edge.target]
        drawing.add(Line(x1, diagram_height - y1, x2, diagram_height - y2))
    for equipment in package.equipment:
        x, y = pos[equipment.id]
        y = diagram_height - y
        if equipment.kind == "pump":
            drawing.add(Circle(x, y, 25, fillColor=colors.white))
        else:
            drawing.add(Rect(x - 45, y - 22, 90, 44, fillColor=colors.white))
        drawing.add(String(x - 50, y - 43, equipment.tag, fontSize=12))
    # Keep one drawing flowable smaller than a page; large graphs are rejected by the API.
    drawing.scale(0.58, 0.58)
    drawing.width *= 0.58
    drawing.height *= 0.58
    flow.append(drawing)
    for line in detail_lines(package):
        flow.append(Paragraph(escape(line), styles["Normal"]))
        flow.append(Spacer(1, 5))
    for page, data in source_crops(package, source):
        flow.append(Paragraph(f"Source context - page {page}", styles["Heading2"]))
        with Image.open(io.BytesIO(data)) as image:
            scale = min(480 / image.width, 600 / image.height)
            flow.append(
                ReportImage(
                    io.BytesIO(data), width=image.width * scale, height=image.height * scale
                )
            )
    SimpleDocTemplate(buffer).build(flow)
    return buffer.getvalue()
