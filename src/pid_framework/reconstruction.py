import copy
import ctypes
import hashlib
import io
from contextlib import closing
from uuid import uuid4

import pypdfium2 as pdfium
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from reportlab.pdfgen.canvas import Canvas

from pid_framework.models import (
    BoundaryRequest,
    Document,
    DrawingConnection,
    DrawingNode,
    Location,
    ReconstructedDrawing,
    ReconstructionModel,
    ReconstructionPreviewRequest,
    ReconstructionRequest,
)
from pid_framework.original import selected_equipment
from pid_framework.pdf import PDF_LOCK


def object_box(obj, page) -> tuple[float, float, float, float]:
    left, bottom, right, top = page.get_bbox()
    x1, y1, x2, y2 = obj.get_bounds()
    return (
        (x1 - left) / (right - left),
        (top - y2) / (top - bottom),
        (x2 - left) / (right - left),
        (top - y1) / (top - bottom),
    )


def contained(inner, outer, tolerance=0.0005) -> bool:
    return (
        inner[0] >= outer[0] - tolerance
        and inner[1] >= outer[1] - tolerance
        and inner[2] <= outer[2] + tolerance
        and inner[3] <= outer[3] + tolerance
    )


def path_segments(obj, page):
    if obj.type != pdfium.raw.FPDF_PAGEOBJ_PATH:
        return []
    matrix = obj.get_matrix()
    left, bottom, right, top = page.get_bbox()
    previous = None
    segments = []
    for index in range(pdfium.raw.FPDFPath_CountSegments(obj)):
        segment = pdfium.raw.FPDFPath_GetPathSegment(obj, index)
        x, y = ctypes.c_float(), ctypes.c_float()
        if not pdfium.raw.FPDFPathSegment_GetPoint(segment, ctypes.byref(x), ctypes.byref(y)):
            raise ValueError("Unable to read a source vector path")
        px = matrix.a * x.value + matrix.c * y.value + matrix.e
        py = matrix.b * x.value + matrix.d * y.value + matrix.f
        point = ((px - left) / (right - left), (top - py) / (top - bottom))
        segment_type = pdfium.raw.FPDFPathSegment_GetType(segment)
        if previous is not None and segment_type == pdfium.raw.FPDF_SEGMENT_LINETO:
            segments.append((previous, point))
        previous = point if segment_type != pdfium.raw.FPDF_SEGMENT_BEZIERTO else None
    return segments


def isolate_stroked_subpaths(page: pdfium.PdfPage) -> None:
    """Split compound stroke-only paths so unrelated pipes are not one indivisible symbol."""
    for obj in list(page.get_objects(max_depth=0)):
        if obj.type != pdfium.raw.FPDF_PAGEOBJ_PATH:
            continue
        fill, stroke = ctypes.c_int(), ctypes.c_int()
        if not pdfium.raw.FPDFPath_GetDrawMode(obj, ctypes.byref(fill), ctypes.byref(stroke)):
            raise ValueError("Cannot read source path drawing mode")
        if fill.value or not stroke.value:
            continue
        groups = []
        for index in range(pdfium.raw.FPDFPath_CountSegments(obj)):
            segment = pdfium.raw.FPDFPath_GetPathSegment(obj, index)
            kind = pdfium.raw.FPDFPathSegment_GetType(segment)
            x, y = ctypes.c_float(), ctypes.c_float()
            if not pdfium.raw.FPDFPathSegment_GetPoint(segment, ctypes.byref(x), ctypes.byref(y)):
                raise ValueError("Cannot read source subpath")
            if kind == pdfium.raw.FPDF_SEGMENT_MOVETO:
                groups.append([])
            if not groups:
                raise ValueError("Source path does not start with a move")
            groups[-1].append(
                (
                    kind,
                    x.value,
                    y.value,
                    bool(pdfium.raw.FPDFPathSegment_GetClose(segment)),
                )
            )
        if len(groups) < 2:
            continue
        width, rgb, dash, phase = stroke_style(obj)
        for group in groups:
            raw = pdfium.raw.FPDFPageObj_CreateNewPath(group[0][1], group[0][2])
            if not raw:
                raise ValueError("Cannot create isolated source path")
            new = pdfium.PdfObject(raw, pdf=page.pdf)
            index = 1
            while index < len(group):
                kind, x, y, close = group[index]
                if kind == pdfium.raw.FPDF_SEGMENT_LINETO:
                    ok = pdfium.raw.FPDFPath_LineTo(new, x, y)
                elif kind == pdfium.raw.FPDF_SEGMENT_BEZIERTO and index + 2 < len(group):
                    controls = group[index : index + 3]
                    ok = pdfium.raw.FPDFPath_BezierTo(
                        new, *(coordinate for point in controls for coordinate in point[1:3])
                    )
                    close = controls[-1][3]
                    index += 2
                else:
                    raise ValueError("Unsupported source path segment")
                if not ok:
                    raise ValueError("Cannot preserve source path geometry")
                if close and not pdfium.raw.FPDFPath_Close(new):
                    raise ValueError("Cannot close source symbol")
                index += 1
            setters = [
                pdfium.raw.FPDFPageObj_SetStrokeWidth(new, width),
                pdfium.raw.FPDFPageObj_SetStrokeColor(new, *(round(c * 255) for c in rgb)),
                pdfium.raw.FPDFPath_SetDrawMode(new, 0, 1),
                pdfium.raw.FPDFPageObj_SetLineCap(new, pdfium.raw.FPDFPageObj_GetLineCap(obj)),
                pdfium.raw.FPDFPageObj_SetLineJoin(new, pdfium.raw.FPDFPageObj_GetLineJoin(obj)),
            ]
            if dash:
                array = (ctypes.c_float * len(dash))(*dash)
                setters.append(pdfium.raw.FPDFPageObj_SetDashArray(new, array, len(dash), phase))
            if not all(setters):
                raise ValueError("Cannot preserve source path style")
            new.set_matrix(obj.get_matrix())
            page.insert_obj(new)
        page.remove_obj(obj)
        obj.close()


def propose_reconstruction(
    document: Document, source: bytes, request: BoundaryRequest
) -> ReconstructionModel:
    equipment = selected_equipment(document, request)
    evidence = [e for e in document.evidence if e.id in equipment.evidence_ids]
    selection = next((e.location for e in evidence if e.origin == "engineer_selection"), None)
    if selection is None:
        raise ValueError(
            "Draw the actual equipment symbol/package outline first. "
            "A confirmed tag label does not define a reconstructable equipment symbol."
        )
    x1, y1, x2, y2 = selection.bbox
    symbol_width, symbol_height = x2 - x1, y2 - y1
    if symbol_width > 0.35 or symbol_height > 0.65:
        raise ValueError("Select the equipment outline, not the complete drawing/network")
    central_x = 0.30
    nodes = [
        DrawingNode(
            id="selected-equipment",
            label=equipment.tag,
            kind=equipment.kind,
            region=selection,
            position=(central_x, 0.24),
        )
    ]
    connections = []
    warnings = [
        "UNCONFIRMED interpretation: inspect every node, port, connection and annotation.",
        "Only horizontal boundary crossings are proposed automatically. "
        "Vertical/curved connections, branches and distant continuation details "
        "require correction.",
        "Crossings are geometric candidates, not verified process or signal connections.",
        "Valves, signals, detached notes and flow arrows may require "
        "additional source-symbol nodes.",
        "No internal details are invented for supplier-defined packages.",
        "No completeness or standards compliance is established by this proposal.",
    ]
    with PDF_LOCK, pdfium.PdfDocument(source) as pdf:
        with closing(pdf[selection.page - 1]) as page:
            isolate_stroked_subpaths(page)
            objects = list(page.get_objects(max_depth=0))
            if any(obj.type == pdfium.raw.FPDF_PAGEOBJ_FORM for obj in objects):
                raise ValueError(
                    "This PDF uses grouped Form XObjects. Native-object reconstruction "
                    "requires flattening those groups; crop export is not substituted."
                )
            candidates = []
            seen = set()
            for object_id, obj in enumerate(objects):
                for start, end in path_segments(obj, page):
                    if abs(start[1] - end[1]) > 0.0008:
                        continue
                    low, high = sorted((start[0], end[0]))
                    y = (start[1] + end[1]) / 2
                    if not y1 + 0.002 < y < y2 - 0.002:
                        continue
                    for side, boundary in (("left", x1), ("right", x2)):
                        reaches = (
                            low < boundary - 0.04 and high >= boundary - 0.012
                            if side == "left"
                            else high > boundary + 0.04 and low <= boundary + 0.012
                        )
                        if not reaches:
                            continue
                        key = (side, round(y, 3))
                        if key in seen:
                            continue
                        seen.add(key)
                        external_x = low if side == "left" else high
                        candidates.append((y, side, external_x, object_id, low, high))
            if not candidates:
                raise ValueError(
                    "No horizontal vector connections cross this selected outline. "
                    "Adjust the equipment outline or author the reconstruction model manually."
                )
            for index, (y, side, external_x, object_id, low, high) in enumerate(
                sorted(candidates), start=1
            ):
                if len(connections) >= 30:
                    raise ValueError("Too many candidate crossings; narrow the equipment outline")
                region = Location(
                    page=selection.page,
                    bbox=(
                        max(0, external_x - 0.055),
                        max(0, y - 0.035),
                        min(1, external_x + 0.012),
                        min(1, y + 0.004),
                    ),
                )
                width, height = region.bbox[2] - region.bbox[0], region.bbox[3] - region.bbox[1]
                gap = max(0.18, high - low)
                target_x = (
                    central_x - gap - width if side == "left" else central_x + symbol_width + gap
                )
                target_y = 0.24 + region.bbox[1] - y1
                if target_x < 0 or target_x + width > 0.76 or target_y < 0:
                    raise ValueError("Proposed symbols do not fit; use a smaller source outline")
                node_id = f"interface-{index}"
                nodes.append(
                    DrawingNode(
                        id=node_id,
                        label=f"External {side} interface {index}",
                        kind="external_interface",
                        region=region,
                        position=(target_x, target_y),
                    )
                )
                connections.append(
                    DrawingConnection(
                        id=f"connection-{index}",
                        source="selected-equipment",
                        target=node_id,
                        source_port=(0 if side == "left" else 1, (y - y1) / symbol_height),
                        target_port=(
                            (external_x - region.bbox[0]) / width,
                            (y - region.bbox[1]) / height,
                        ),
                        source_region=Location(
                            page=selection.page,
                            bbox=(
                                max(0, low),
                                max(0, y - 0.003),
                                min(1, high),
                                min(1, y + 0.003),
                            ),
                        ),
                        source_object_id=object_id,
                        description="Boundary-crossing source path; engineer must verify semantics",
                    )
                )
                # Move source lettering independently of rerouted pipes.
                for text_index, text in enumerate(objects):
                    if text.type != pdfium.raw.FPDF_PAGEOBJ_TEXT:
                        continue
                    box = object_box(text, page)
                    corridor = (low, y - 0.035, high, y + 0.008)
                    if not contained(box, corridor) or contained(box, selection.bbox):
                        continue
                    if any(contained(box, node.region.bbox) for node in nodes):
                        continue
                    if any(node.id == f"text-{text_index}" for node in nodes):
                        continue
                    destination_y = 0.24 + box[1] - y1
                    gap_start = central_x + symbol_width if side == "right" else target_x + width
                    destination_x = gap_start + box[0] - low + (gap - (high - low)) / 2
                    if destination_x < 0:
                        continue
                    nodes.append(
                        DrawingNode(
                            id=f"text-{text_index}",
                            label="Source line annotation",
                            kind="annotation",
                            region=Location(page=selection.page, bbox=box),
                            position=(destination_x, destination_y),
                            text_only=True,
                        )
                    )
            # Detect the full-height notes/title-block separator rather than assuming a template.
            separators = [
                object_box(obj, page)[0]
                for obj in objects
                if obj.type == pdfium.raw.FPDF_PAGEOBJ_PATH
                and object_box(obj, page)[2] - object_box(obj, page)[0] < 0.002
                and object_box(obj, page)[3] - object_box(obj, page)[1] > 0.65
                and 0.65 < object_box(obj, page)[0] < 0.9
            ]
            template = [
                Location(page=selection.page, bbox=(0, 0, 1, 0.02)),
                Location(page=selection.page, bbox=(0, 0.98, 1, 1)),
                Location(page=selection.page, bbox=(0, 0, 0.01, 1)),
            ]
            if separators:
                template.append(
                    Location(page=selection.page, bbox=(max(0, min(separators) - 0.01), 0, 1, 1))
                )
            else:
                warnings.append("Title/notes panel not detected; add its source region explicitly.")
    return ReconstructionModel(
        page=selection.page,
        nodes=nodes,
        connections=connections,
        template_regions=template,
        warnings=warnings,
    )


def create_reconstruction(
    document: Document, request: ReconstructionPreviewRequest
) -> ReconstructedDrawing:
    equipment = selected_equipment(document, request)
    source_regions = [
        evidence.location
        for evidence in document.evidence
        if evidence.id in equipment.evidence_ids and evidence.origin == "engineer_selection"
    ]
    if not source_regions or not any(
        not node.text_only
        and node.region.page == region.page
        and contained(region.bbox, node.region.bbox)
        for region in source_regions
        for node in request.drawing.nodes
    ):
        raise ValueError("Selected equipment's confirmed source symbol must remain in the drawing")
    if request.drawing.page > len(document.pages):
        raise ValueError("Drawing source page is outside document")
    for region in request.drawing.template_regions:
        if region.page != request.drawing.page:
            raise ValueError("Template must come from the same source sheet")
    return ReconstructedDrawing(
        id=str(uuid4()),
        document_id=document.id,
        document_version=document.version,
        source_sha256=document.sha256,
        revision=document.revision,
        equipment_id=request.equipment_id,
        drawing=request.drawing,
        engineer_confirmed=isinstance(request, ReconstructionRequest),
        status=(
            "reconstructed_draft_not_for_construction"
            if isinstance(request, ReconstructionRequest)
            else "unconfirmed_preview"
        ),
    )


def source_object_inventory(source: bytes, page_number: int) -> list[dict[str, object]]:
    inventory = []
    with PDF_LOCK, pdfium.PdfDocument(source) as pdf:
        if not 1 <= page_number <= len(pdf):
            raise ValueError("Source page is outside document")
        with closing(pdf[page_number - 1]) as page:
            isolate_stroked_subpaths(page)
            objects = list(page.get_objects(max_depth=0))
            with closing(page.get_textpage()) as textpage:
                for index, obj in enumerate(objects):
                    entry: dict[str, object] = {
                        "id": index,
                        "type": obj.type,
                        "bbox": object_box(obj, page),
                    }
                    if obj.type == pdfium.raw.FPDF_PAGEOBJ_TEXT:
                        entry["text"] = textpage.get_text_bounded(*obj.get_bounds())
                    elif obj.type == pdfium.raw.FPDF_PAGEOBJ_PATH:
                        fill, stroke = ctypes.c_int(), ctypes.c_int()
                        if not pdfium.raw.FPDFPath_GetDrawMode(
                            obj, ctypes.byref(fill), ctypes.byref(stroke)
                        ):
                            raise ValueError("Cannot read source drawing mode")
                        if stroke.value:
                            width, color, dash, phase = stroke_style(obj)
                            entry["stroke"] = {
                                "width": width,
                                "rgba": color,
                                "dash": dash,
                                "phase": phase,
                            }
                    inventory.append(entry)
    return inventory


def stroke_style(obj):
    width = ctypes.c_float()
    if not pdfium.raw.FPDFPageObj_GetStrokeWidth(obj, ctypes.byref(width)):
        raise ValueError("Source connection has no readable stroke width")
    rgba = [ctypes.c_uint() for _ in range(4)]
    if not pdfium.raw.FPDFPageObj_GetStrokeColor(obj, *(ctypes.byref(c) for c in rgba)):
        raise ValueError("Source connection has no readable stroke color")
    count = pdfium.raw.FPDFPageObj_GetDashCount(obj)
    if count < 0:
        raise ValueError("Cannot read source connection dash pattern")
    dash = (ctypes.c_float * count)()
    if count and not pdfium.raw.FPDFPageObj_GetDashArray(obj, dash, count):
        raise ValueError("Cannot read source connection dash pattern")
    phase = ctypes.c_float()
    if not pdfium.raw.FPDFPageObj_GetDashPhase(obj, ctypes.byref(phase)):
        raise ValueError("Cannot read source connection dash phase")
    return width.value, [c.value / 255 for c in rgba], list(dash), phase.value


def render_reconstruction(result: ReconstructedDrawing, source: bytes) -> bytes:
    if hashlib.sha256(source).hexdigest() != result.source_sha256:
        raise ValueError("Source PDF failed checksum validation")
    model = result.drawing
    native_output = io.BytesIO()
    routing_output = io.BytesIO()
    with PDF_LOCK, pdfium.PdfDocument(source) as pdf:
        with closing(pdf[model.page - 1]) as page:
            isolate_stroked_subpaths(page)
            if page.get_rotation() != 0:
                raise ValueError("Normalize rotated pages before reconstructing")
            left, bottom, right, top = page.get_bbox()
            if abs(left) > 0.01 or abs(bottom) > 0.01:
                raise ValueError("Normalize nonzero source page origins before reconstruction")
            width, height = page.get_size()
            objects = list(page.get_objects(max_depth=0))
            boxes = [object_box(obj, page) for obj in objects]
            assigned: dict[int, tuple[float, float]] = {}
            for node in model.nodes:
                x1, y1, x2, y2 = node.region.bbox
                nx, ny = node.position
                if nx + x2 - x1 > 0.98 or ny + y2 - y1 > 0.95:
                    raise ValueError(f"Node {node.id} does not fit on the new sheet")
                moved = []
                for index, obj in enumerate(objects):
                    if node.text_only and obj.type != pdfium.raw.FPDF_PAGEOBJ_TEXT:
                        continue
                    if contained(boxes[index], node.region.bbox):
                        if obj.type == pdfium.raw.FPDF_PAGEOBJ_FORM:
                            raise ValueError(
                                "Grouped PDF forms cannot be relocated as isolated symbols"
                            )
                        if index in assigned:
                            raise ValueError(
                                f"Overlapping source symbols at {node.id}; "
                                "separate their regions before confirming"
                            )
                        assigned[index] = ((nx - x1) * width, (y1 - ny) * height)
                        moved.append(index)
                if not moved:
                    raise ValueError(f"No complete native PDF objects inside symbol {node.id}")
            canvas = Canvas(routing_output, pagesize=(width, height))
            nodes = {node.id: node for node in model.nodes}
            for edge in model.connections:
                if edge.source_region.page != model.page or edge.source_object_id >= len(objects):
                    raise ValueError(f"Invalid source vector on {edge.id}")
                obj = objects[edge.source_object_id]
                if obj.type != pdfium.raw.FPDF_PAGEOBJ_PATH:
                    raise ValueError(f"Connection {edge.id} must cite a source vector path")
                a, b = boxes[edge.source_object_id], edge.source_region.bbox
                if a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3]:
                    raise ValueError(f"Source style evidence does not overlap {edge.id}")
                line_width, color, dash, phase = stroke_style(obj)
                canvas.setLineWidth(line_width)
                matrix = obj.get_matrix()
                scale = abs(matrix.a * matrix.d - matrix.b * matrix.c) ** 0.5
                canvas.setLineWidth(line_width * scale)
                canvas.setStrokeColorRGB(*color[:3])
                canvas.setStrokeAlpha(color[3])
                canvas.setLineCap(pdfium.raw.FPDFPageObj_GetLineCap(obj))
                canvas.setLineJoin(pdfium.raw.FPDFPageObj_GetLineJoin(obj))
                canvas.setDash([value * scale for value in dash], phase * scale)
                points = []
                for node_id, port in (
                    (edge.source, edge.source_port),
                    (edge.target, edge.target_port),
                ):
                    node = nodes[node_id]
                    x1, y1, x2, y2 = node.region.bbox
                    points.append(
                        (
                            (node.position[0] + port[0] * (x2 - x1)) * width,
                            (1 - node.position[1] - port[1] * (y2 - y1)) * height,
                        )
                    )
                (sx, sy), (tx, ty) = points
                middle = (sx + tx) / 2
                path = canvas.beginPath()
                path.moveTo(sx, sy)
                path.lineTo(middle, sy)
                path.lineTo(middle, ty)
                path.lineTo(tx, ty)
                canvas.drawPath(path)
            canvas.showPage()
            canvas.setFillColorRGB(0.65, 0.08, 0.08)
            canvas.setFont("Helvetica", 11)
            canvas.drawString(
                width * 0.04,
                height * 0.955,
                f"{'RECONSTRUCTED DRAFT' if result.engineer_confirmed else 'UNCONFIRMED PREVIEW'}"
                f" - NOT FOR CONSTRUCTION - SOURCE REV {result.revision}",
            )
            canvas.save()
            for index, obj in enumerate(objects):
                if index in assigned:
                    dx, dy = assigned[index]
                    obj.set_matrix(obj.get_matrix().translate(dx, dy))
                else:
                    page.remove_obj(obj)
                    obj.close()
            page.gen_content()
            with pdfium.PdfDocument.new() as output:
                output.import_pages(pdf, pages=[model.page - 1])
                output.save(native_output)
    native = PdfReader(io.BytesIO(native_output.getvalue()))
    routes = PdfReader(io.BytesIO(routing_output.getvalue()))
    writer = PdfWriter()
    writer.add_blank_page(width=width, height=height)
    template = PdfReader(io.BytesIO(source))
    for region in model.template_regions:
        original_page = copy.copy(template.pages[region.page - 1])
        x1, y1, x2, y2 = region.bbox
        original_page.cropbox = RectangleObject(
            (x1 * width, (1 - y2) * height, x2 * width, (1 - y1) * height)
        )
        writer.pages[0].merge_page(original_page)
    writer.pages[0].merge_page(routes.pages[0])
    # Native symbols/lettering remain above the newly routed source-style connections.
    writer.pages[0].merge_page(native.pages[0])
    writer.pages[0].merge_page(routes.pages[1])
    writer.add_metadata(
        {
            "/Title": "Reconstructed equipment interface P&ID - draft",
            "/Subject": f"Source {result.document_id}; SHA256 {result.source_sha256}",
        }
    )
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()
