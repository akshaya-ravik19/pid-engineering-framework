import io
from contextlib import closing

import pypdfium2 as pdfium
from fastapi.testclient import TestClient
from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas

from pid_framework.api import create_app
from pid_framework.connected import connected_drawing
from pid_framework.models import BoundaryRequest, Equipment, Evidence, Location
from pid_framework.pdf import extract_document


def system_source():
    output = io.BytesIO()
    canvas = Canvas(output, pagesize=(1000, 700))
    canvas.setFont("Helvetica", 8)
    canvas.circle(150, 350, 25)
    canvas.drawString(136, 348, "P-101")
    canvas.line(175, 350, 300, 350)
    for coordinates in [
        [(300, 340), (300, 360), (320, 350)],
        [(340, 340), (340, 360), (320, 350)],
    ]:
        path = canvas.beginPath()
        path.moveTo(*coordinates[0])
        for point in coordinates[1:]:
            path.lineTo(*point)
        path.close()
        canvas.drawPath(path)
    canvas.drawString(302, 325, "HV-101")
    canvas.line(340, 350, 600, 350)
    canvas.line(450, 350, 450, 500)
    canvas.circle(450, 515, 15)
    canvas.drawString(438, 513, "PT-101")
    canvas.rect(600, 310, 80, 80)
    canvas.drawString(620, 350, "V-102")
    path = canvas.beginPath()
    path.moveTo(680, 350)
    path.lineTo(800, 350)
    path.lineTo(800, 230)
    path.lineTo(950, 230)
    canvas.drawPath(path)
    arrow = canvas.beginPath()
    arrow.moveTo(950, 220)
    arrow.lineTo(970, 230)
    arrow.lineTo(950, 240)
    arrow.close()
    canvas.drawPath(arrow)
    canvas.drawString(915, 250, "TO DWG 2")
    canvas.line(970, 230, 990, 230)  # Must not traverse past the recognized continuation.
    # This independent line crosses the main pipe, without endpoints or a junction dot.
    canvas.line(500, 250, 500, 450)
    canvas.circle(500, 465, 15)
    canvas.circle(500, 235, 15)
    canvas.drawString(480, 485, "DISCONNECTED")
    canvas.drawString(700, 600, "UNRELATED NOTE")
    canvas.showPage()
    canvas.drawString(100, 300, "OFFSHEET-SENTINEL")
    canvas.save()
    source = output.getvalue()
    document = extract_document(source, "system.pdf", "A")
    region = Location(page=1, bbox=(0.123, 0.462, 0.177, 0.538))
    document.evidence.append(
        Evidence(
            id="selected-pump",
            text="Selected pump outline",
            location=region,
            origin="engineer_selection",
        )
    )
    document.graph.equipment.append(
        Equipment(
            id="pump", tag="P-101", kind="pump", confirmed=True, evidence_ids=["selected-pump"]
        )
    )
    return source, document


def test_connected_trace_includes_multi_hop_valve_vessel_and_vertical_instrument():
    source, document = system_source()
    drawing = connected_drawing(
        document, source, BoundaryRequest(equipment_id="pump", expected_version=1)
    )
    assert len(drawing.connections) >= 7
    labels = " ".join(node.label for node in drawing.nodes)
    assert "HV-101" in labels
    assert "V-102" in labels
    assert "PT-101" in labels
    assert "DISCONNECTED" not in labels
    assert "UNRELATED NOTE" not in labels
    assert "OFFSHEET-SENTINEL" not in labels
    assert all(node.region.page == 1 for node in drawing.nodes)
    boundaries = [node for node in drawing.nodes if node.kind == "continuation_boundary"]
    assert any("TO DWG 2" in node.label for node in boundaries)
    assert all(
        point[0] <= 0.9701 for edge in drawing.connections for point in edge.source_points or []
    )
    # Source x=500 contains only the disconnected crossing system.
    assert not any(
        abs(node.region.bbox[0] - 0.5) < 0.001 and node.kind == "junction" for node in drawing.nodes
    )


def test_generated_connected_system_is_vector_relaid_and_stops_at_page_boundary(tmp_path):
    source, document = system_source()
    from pid_framework.store import Store

    store = Store(tmp_path)
    store.save_source(document, source)
    store.save("document", document)
    with TestClient(create_app(tmp_path)) as client:
        response = client.post(
            f"/documents/{document.id}/generations",
            json={"equipment_id": "pump", "expected_version": 1},
        )
        assert response.status_code == 201, response.text
        result = response.json()
        drawing = result["reconstruction"]["drawing"]
        assert drawing["sheet_size"][0] > drawing["source_size"][0]
        assert "same-page pipelines" in result["summary"]
        output = client.get(f"/generations/{result['id']}/drawing.pdf")
        assert output.status_code == 200
        parsed = PdfReader(io.BytesIO(output.content))
        assert len(parsed.pages) >= 2
        assert not parsed.pages[0].images
        text = parsed.pages[0].extract_text()
        for label in ["P-101", "HV-101", "V-102", "PT-101", "TO DWG 2"]:
            assert label in text
        for excluded in ["DISCONNECTED", "UNRELATED NOTE", "OFFSHEET-SENTINEL"]:
            assert excluded not in "".join(page.extract_text() for page in parsed.pages)
        assert "Same-page boundaries and continuations" in "".join(
            page.extract_text() for page in parsed.pages[1:]
        )
        with pdfium.PdfDocument(output.content) as pdf, closing(pdf[0]) as page:
            assert page.get_size()[0] > 1000
            with closing(page.get_textpage()) as textpage:
                # Glyph size is retained while source positions and pipe spacing change.
                index = textpage.get_text_range().index("V-102")
                box = textpage.get_charbox(index)
                assert 4 < box[3] - box[1] < 9
            bitmap = page.render(scale=0.5)
            try:
                assert bitmap.width > 500
            finally:
                bitmap.close()
