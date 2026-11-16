import ctypes
import math
import re
from collections import defaultdict, deque
from contextlib import closing
from dataclasses import dataclass

import pypdfium2 as pdfium

from pid_framework.models import (
    BoundaryRequest,
    Document,
    DrawingConnection,
    DrawingNode,
    Location,
    ReconstructionModel,
)
from pid_framework.original import pdf_coordinates, selected_equipment
from pid_framework.pdf import PDF_LOCK
from pid_framework.reconstruction import (
    contained,
    isolate_stroked_subpaths,
    object_box,
    path_segments,
)

Point = tuple[float, float]
Box = tuple[float, float, float, float]
CONTINUATION = re.compile(
    r"\b(?:OFF[ -]?PAGE|CONTINUED\s+ON|CONTINUATION|(?:TO|FROM)\s+(?:SHEET|DWG|DRAWING))\b",
    re.IGNORECASE,
)


@dataclass
class Symbol:
    id: str
    box: Box
    objects: list[int]
    label: str
    kind: str = "supporting_symbol"


def union_box(boxes: list[Box]) -> Box:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def padded(box: Box, amount: float = 0.00001) -> Box:
    return (
        max(0, box[0] - amount),
        max(0, box[1] - amount),
        min(1, box[2] + amount),
        min(1, box[3] + amount),
    )


def distance_to_segment(point: Point, a: Point, b: Point) -> tuple[float, float]:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    t = max(0, min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length))
    return math.hypot(point[0] - a[0] - t * dx, point[1] - a[1] - t * dy), t


def box_distance(a: Box, b: Box, width: float, height: float) -> float:
    return math.hypot(
        max(0, a[0] - b[2], b[0] - a[2]) * width,
        max(0, a[1] - b[3], b[1] - a[3]) * height,
    )


def closed_path(obj) -> bool:
    count = pdfium.raw.FPDFPath_CountSegments(obj)
    if not count:
        return False
    last = pdfium.raw.FPDFPath_GetPathSegment(obj, count - 1)
    if pdfium.raw.FPDFPathSegment_GetClose(last):
        return True
    points = []
    for index in (0, count - 1):
        x, y = ctypes.c_float(), ctypes.c_float()
        if not pdfium.raw.FPDFPathSegment_GetPoint(
            pdfium.raw.FPDFPath_GetPathSegment(obj, index), ctypes.byref(x), ctypes.byref(y)
        ):
            raise ValueError("Cannot read source path endpoints")
        points.append((x.value, y.value))
    return count > 2 and math.dist(points[0], points[1]) < 0.0001


def rect_splits(a: Point, b: Point, box: Box) -> list[float]:
    values = []
    for axis, low, high in ((0, box[0], box[2]), (1, box[1], box[3])):
        delta = b[axis] - a[axis]
        if abs(delta) < 1e-12:
            continue
        for boundary in (low, high):
            t = (boundary - a[axis]) / delta
            other = a[1 - axis] + t * (b[1 - axis] - a[1 - axis])
            if 0 < t < 1 and box[1 - axis] <= other <= box[3 - axis]:
                values.append(t)
    return values


def connected_drawing(
    document: Document, source: bytes, request: BoundaryRequest
) -> ReconstructionModel:
    equipment = selected_equipment(document, request)
    selection = next(
        (
            item.location
            for item in document.evidence
            if item.id in equipment.evidence_ids and item.origin == "engineer_selection"
        ),
        None,
    )
    if selection is None:
        raise ValueError("Draw the actual equipment symbol/package outline first.")
    if selection.bbox[2] - selection.bbox[0] > 0.35:
        raise ValueError("Select the equipment outline, not the complete drawing/network")
    with PDF_LOCK, pdfium.PdfDocument(source) as pdf:
        with closing(pdf[selection.page - 1]) as page:
            isolate_stroked_subpaths(page)
            width, height = page.get_size()
            objects = list(page.get_objects(max_depth=0))
            if any(obj.type == pdfium.raw.FPDF_PAGEOBJ_FORM for obj in objects):
                raise ValueError("Grouped Form XObjects are unsupported; no crop is substituted.")
            if len(objects) > 20000:
                raise ValueError("Source page exceeds the 20000-object tracing limit")
            boxes = [object_box(obj, page) for obj in objects]
            symbols = [
                Symbol(
                    "selected-equipment",
                    selection.bbox,
                    [i for i, box in enumerate(boxes) if contained(box, selection.bbox)],
                    equipment.tag,
                    equipment.kind,
                )
            ]
            selected_ids = set(symbols[0].objects)
            for index, obj in enumerate(objects):
                if index in selected_ids or obj.type != pdfium.raw.FPDF_PAGEOBJ_PATH:
                    continue
                box = padded(boxes[index])
                fill, stroke = ctypes.c_int(), ctypes.c_int()
                if not pdfium.raw.FPDFPath_GetDrawMode(
                    obj, ctypes.byref(fill), ctypes.byref(stroke)
                ):
                    raise ValueError("Cannot read source symbol drawing mode")
                if (closed_path(obj) or fill.value) and (box[2] - box[0]) * (
                    box[3] - box[1]
                ) < 0.35:
                    symbols.append(
                        Symbol(f"symbol-{index}", box, [index], f"Supporting source symbol {index}")
                    )
            if len(symbols) > 1000:
                raise ValueError("Source page exceeds the 1000-symbol tracing limit")
            # Touching closed strokes (for example the two halves of a valve) form one symbol.
            changed = True
            while changed:
                changed = False
                for i in range(1, len(symbols)):
                    for j in range(i + 1, len(symbols)):
                        if box_distance(symbols[i].box, symbols[j].box, width, height) < 0.5:
                            symbols[i].box = union_box([symbols[i].box, symbols[j].box])
                            symbols[i].objects.extend(symbols[j].objects)
                            symbols.pop(j)
                            changed = True
                            break
                    if changed:
                        break
            text = {}
            with closing(page.get_textpage()) as textpage:
                for index, obj in enumerate(objects):
                    if obj.type == pdfium.raw.FPDF_PAGEOBJ_TEXT:
                        region = Location(page=selection.page, bbox=padded(boxes[index]))
                        value = textpage.get_text_bounded(*pdf_coordinates(page, region)).strip()
                        if value:
                            text[index] = value
                for symbol in symbols[1:]:
                    nearby = [
                        value
                        for index, value in text.items()
                        if box_distance(symbol.box, boxes[index], width, height) <= 16
                    ]
                    if nearby:
                        symbol.label = " | ".join(nearby)[:4000]
                    if CONTINUATION.search(symbol.label):
                        symbol.kind = "continuation_boundary"
            assigned = set()
            for symbol in symbols:
                for index, box in enumerate(boxes):
                    if index not in assigned and contained(box, symbol.box):
                        symbol.objects.append(index)
                        assigned.add(index)
                symbol.objects = sorted(set(symbol.objects))
                assigned.update(symbol.objects)
            segments = []
            skipped_curves = False
            for index, obj in enumerate(objects):
                if index in assigned or obj.type != pdfium.raw.FPDF_PAGEOBJ_PATH:
                    continue
                if closed_path(obj):
                    continue  # Frames are not pipelines.
                fill, stroke = ctypes.c_int(), ctypes.c_int()
                if not pdfium.raw.FPDFPath_GetDrawMode(
                    obj, ctypes.byref(fill), ctypes.byref(stroke)
                ):
                    raise ValueError("Cannot read source line drawing mode")
                if not stroke.value:
                    continue
                count = pdfium.raw.FPDFPath_CountSegments(obj)
                if any(
                    pdfium.raw.FPDFPathSegment_GetType(pdfium.raw.FPDFPath_GetPathSegment(obj, i))
                    == pdfium.raw.FPDF_SEGMENT_BEZIERTO
                    for i in range(count)
                ):
                    skipped_curves = True
                    continue
                for a, b in path_segments(obj, page):
                    if math.hypot((a[0] - b[0]) * width, (a[1] - b[1]) * height) > 0.1:
                        segments.append((index, a, b))
            if len(segments) > 6000:
                raise ValueError("Source page exceeds the 6000-segment tracing limit")
            drawing = trace_network(symbols, segments, selection, width, height)
            used = {ref for node in drawing.nodes for ref in node.source_object_ids or []}
            connected_segments = [
                edge.source_points for edge in drawing.connections if edge.source_points is not None
            ]
            for index, value in text.items():
                if index in used:
                    continue
                box = padded(boxes[index])
                center = ((box[0] + box[2]) * width / 2, (box[1] + box[3]) * height / 2)
                near_symbol = any(
                    node.source_object_ids
                    and box_distance(node.region.bbox, box, width, height) <= 16
                    for node in drawing.nodes
                )
                near_pipe = any(
                    min(
                        distance_to_segment(
                            point,
                            (a[0] * width, a[1] * height),
                            (b[0] * width, b[1] * height),
                        )[0]
                        for point in (
                            center,
                            (box[0] * width, box[1] * height),
                            (box[0] * width, box[3] * height),
                            (box[2] * width, box[1] * height),
                            (box[2] * width, box[3] * height),
                        )
                    )
                    <= 14
                    for a, b in connected_segments
                )
                if near_symbol or near_pipe:
                    drawing.nodes.append(
                        DrawingNode(
                            id=f"annotation-{index}",
                            label=value[:4000],
                            kind="annotation",
                            region=Location(page=selection.page, bbox=box),
                            position=(0, 0),
                            text_only=True,
                            source_object_ids=[index],
                        )
                    )
            if skipped_curves:
                drawing.warnings.append(
                    "Open curved vector paths were not traced; "
                    "connected coverage may be incomplete."
                )
            return arrange_network(drawing, width, height)


def trace_network(
    symbols: list[Symbol],
    segments: list[tuple[int, Point, Point]],
    selection: Location,
    width: float,
    height: float,
) -> ReconstructionModel:
    tolerance = 0.8
    nodes = {
        symbol.id: DrawingNode(
            id=symbol.id,
            label=symbol.label,
            kind=symbol.kind,
            region=Location(page=selection.page, bbox=symbol.box),
            position=(0, 0),
            source_object_ids=symbol.objects,
        )
        for symbol in symbols
    }
    junctions: dict[tuple[int, int], list[tuple[Point, str]]] = defaultdict(list)

    def endpoint(point: Point) -> tuple[str, Point]:
        matches = [
            symbol
            for symbol in symbols
            if symbol.box[0] - tolerance / width <= point[0] <= symbol.box[2] + tolerance / width
            and symbol.box[1] - tolerance / height <= point[1] <= symbol.box[3] + tolerance / height
        ]
        if matches:
            symbol = min(
                matches,
                key=lambda s: (
                    s.id != "selected-equipment",
                    (s.box[2] - s.box[0]) * (s.box[3] - s.box[1]),
                ),
            )
            box = symbol.box
            return symbol.id, (
                max(0, min(1, (point[0] - box[0]) / (box[2] - box[0]))),
                max(0, min(1, (point[1] - box[1]) / (box[3] - box[1]))),
            )
        cell = (math.floor(point[0] * width / tolerance), math.floor(point[1] * height / tolerance))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for original, node_id in junctions[(cell[0] + dx, cell[1] + dy)]:
                    if (
                        math.hypot(
                            (original[0] - point[0]) * width, (original[1] - point[1]) * height
                        )
                        <= tolerance
                    ):
                        return node_id, (0.5, 0.5)
        node_id = f"junction-{len(junctions)}-{len(nodes)}"
        region = padded((point[0], point[1], point[0], point[1]))
        nodes[node_id] = DrawingNode(
            id=node_id,
            label="Source line junction",
            kind="junction",
            region=Location(page=selection.page, bbox=region),
            position=(0, 0),
            source_object_ids=[],
        )
        junctions[cell].append((point, node_id))
        return node_id, (0.5, 0.5)

    splits = [{0.0, 1.0} for _ in segments]
    grid: dict[tuple[int, int], set[int]] = defaultdict(set)
    cell_size = 24
    for index, (_, a, b) in enumerate(segments):
        x1, x2 = sorted((a[0] * width, b[0] * width))
        y1, y2 = sorted((a[1] * height, b[1] * height))
        for x in range(
            math.floor((x1 - tolerance) / cell_size), math.floor((x2 + tolerance) / cell_size) + 1
        ):
            for y in range(
                math.floor((y1 - tolerance) / cell_size),
                math.floor((y2 + tolerance) / cell_size) + 1,
            ):
                grid[(x, y)].add(index)
    for _, a, b in segments:
        for point in (a, b):
            px, py = point[0] * width, point[1] * height
            for index in grid[(math.floor(px / cell_size), math.floor(py / cell_size))]:
                _, start, end = segments[index]
                distance, t = distance_to_segment(
                    (px, py),
                    (start[0] * width, start[1] * height),
                    (end[0] * width, end[1] * height),
                )
                if distance <= tolerance:
                    splits[index].add(t)
    connections = []
    for index, (object_id, a, b) in enumerate(segments):
        for symbol in symbols:
            splits[index].update(rect_splits(a, b, symbol.box))
        values = sorted(splits[index])
        for low, high in zip(values, values[1:]):
            if (high - low) * math.hypot(
                (b[0] - a[0]) * width, (b[1] - a[1]) * height
            ) <= tolerance:
                continue
            start = (a[0] + low * (b[0] - a[0]), a[1] + low * (b[1] - a[1]))
            end = (a[0] + high * (b[0] - a[0]), a[1] + high * (b[1] - a[1]))
            source, sp = endpoint(start)
            target, tp = endpoint(end)
            if source == target:
                continue
            connections.append(
                DrawingConnection(
                    id=f"pipe-{len(connections) + 1}",
                    source=source,
                    target=target,
                    source_port=sp,
                    target_port=tp,
                    source_region=Location(
                        page=selection.page,
                        bbox=padded(
                            (
                                min(start[0], end[0]),
                                min(start[1], end[1]),
                                max(start[0], end[0]),
                                max(start[1], end[1]),
                            )
                        ),
                    ),
                    source_object_id=object_id,
                    description="Same-page vector connectivity candidate; not process-verified",
                    source_points=(start, end),
                )
            )
    adjacency = defaultdict(list)
    for index, edge in enumerate(connections):
        adjacency[edge.source].append((edge.target, index))
        adjacency[edge.target].append((edge.source, index))
    reached, included = {"selected-equipment"}, set()
    queue = deque(reached)
    while queue:
        node_id = queue.popleft()
        if nodes[node_id].kind == "continuation_boundary":
            continue
        for neighbor, edge in adjacency[node_id]:
            included.add(edge)
            if neighbor not in reached:
                reached.add(neighbor)
                queue.append(neighbor)
    edges = [connections[i] for i in sorted(included)]
    degree = defaultdict(int)
    for edge in edges:
        degree[edge.source] += 1
        degree[edge.target] += 1
    boundary = 0
    for node_id in sorted(reached):
        node = nodes[node_id]
        if node.kind == "continuation_boundary" or (
            node.kind == "junction" and degree[node_id] == 1
        ):
            boundary += 1
            node.kind = "continuation_boundary"
            source_label = node.label if node.source_object_ids else "Unresolved source line end"
            node.label = (
                f"B{boundary}: {source_label}. "
                f"Source page {selection.page}; off-page continuation is not followed."
            )[:4000]
    warnings = [
        "The draft includes the traceable same-page vector network connected to the selected "
        "equipment, including supporting closed-path symbols and nearby source lettering.",
        "Connectivity is inferred from source geometry, not independently verified process logic.",
        "Endpoint contacts and T-junctions are traced. Interior-only line crossings do not join.",
        "Gaps larger than 0.8 PDF points, raster content, detached notes and ambiguous "
        "crossing conventions may leave parts of the system untraced. Open/unrecognized "
        "symbols may be missed or interpreted as linework.",
        "Supporting symbol categories and nearby label associations are geometric candidates, "
        "not independently verified equipment classification.",
        "B labels mark source line ends or recognized continuation symbols. No other source "
        "page is traversed, and missing continuation destinations are not invented.",
        "No completeness, safety, standards compliance or engineering approval is established.",
    ]
    if not edges:
        warnings.append(
            "No supported vector interfaces were found. This is a symbol-only draft, "
            "not a complete P&ID."
        )
    return ReconstructionModel(
        page=selection.page,
        nodes=[nodes[key] for key in nodes if key in reached],
        connections=edges,
        warnings=warnings,
    )


def arrange_network(
    drawing: ReconstructionModel, width: float, height: float
) -> ReconstructionModel:
    bounds = union_box([node.region.bbox for node in drawing.nodes])
    factor = 1.7
    sheet_width = max(width, (bounds[2] - bounds[0]) * width * factor / 0.88)
    sheet_height = max(height, (bounds[3] - bounds[1]) * height * factor / 0.76)
    drawing.source_size = (width, height)
    drawing.sheet_size = (sheet_width, sheet_height)
    for node in drawing.nodes:
        x1, y1, x2, y2 = node.region.bbox
        node.position = (
            0.05 + (((x1 + x2) / 2 - bounds[0]) * factor - (x2 - x1) / 2) * width / sheet_width,
            0.13 + (((y1 + y2) / 2 - bounds[1]) * factor - (y2 - y1) / 2) * height / sheet_height,
        )
    # Validate appended annotations and expanded page sizes, not just the initial graph.
    return ReconstructionModel.model_validate(drawing.model_dump())
