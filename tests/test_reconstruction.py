import io
from contextlib import closing

import numpy as np
import pypdfium2 as pdfium
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas

from pid_framework.api import create_app
from pid_framework.models import (
    BoundaryRequest,
    Equipment,
    Evidence,
    Location,
    ReconstructionModel,
    ReconstructionPreviewRequest,
    ReconstructionRequest,
)
from pid_framework.pdf import extract_document
from pid_framework.reconstruction import (
    create_reconstruction,
    propose_reconstruction,
    render_reconstruction,
)


def source_bytes():
    output = io.BytesIO()
    canvas = Canvas(output, pagesize=(1000, 700))
    canvas.setStrokeColorRGB(0.1, 0.3, 0.7)
    canvas.setLineWidth(0.8)
    canvas.rect(100, 180, 120, 260)
    canvas.drawString(120, 300, "V-101")
    canvas.setDash([6, 2])
    path = canvas.beginPath()
    path.moveTo(220, 360)
    path.lineTo(480, 360)
    path.moveTo(220, 260)
    path.lineTo(480, 260)
    canvas.drawPath(path)  # Deliberately grouped independent source connections.
    canvas.setDash()
    canvas.rect(450, 360, 40, 20)
    canvas.rect(450, 260, 40, 20)
    canvas.drawString(300, 372, "SOURCE SPEC 10 bar")
    canvas.drawString(300, 272, "SOURCE SPEC 20 bar")
    canvas.drawString(800, 200, "SOURCE NOTES")
    canvas.line(780, 20, 780, 680)
    canvas.save()
    return output.getvalue()


def source_document():
    source = source_bytes()
    document = extract_document(source, "vessel.pdf", "A")
    region = Location(page=1, bbox=(0.098, 0.369, 0.222, 0.745))
    document.evidence.append(
        Evidence(
            id="vessel-symbol",
            location=region,
            text="Selected vessel package outline",
            origin="engineer_selection",
        )
    )
    document.graph.equipment.append(
        Equipment(
            id="vessel",
            tag="V-101",
            kind="vessel",
            confirmed=True,
            evidence_ids=["vessel-symbol"],
        )
    )
    return source, document


def test_equipment_kind_is_not_pump_restricted_and_names_are_discovered():
    output = io.BytesIO()
    canvas = Canvas(output)
    canvas.drawString(50, 700, "HYDRAULIC POWER UNIT (HPU)")
    canvas.drawString(50, 650, "TOPSIDE UMBILICAL TERMINATION UNIT (TUTU)")
    canvas.save()
    document = extract_document(output.getvalue(), "packages.pdf", "A")
    assert any("HYDRAULIC" in item.tag for item in document.graph.equipment)
    assert any("TOPSIDE" in item.tag for item in document.graph.equipment)
    source, document = source_document()
    document.graph.equipment[-1].kind = "custom_vendor_package"
    model = propose_reconstruction(
        document, source, BoundaryRequest(equipment_id="vessel", expected_version=1)
    )
    assert model.nodes[0].kind == "custom_vendor_package"


def test_compound_pipes_become_distinct_connections_and_native_symbols_move():
    source, document = source_document()
    model = propose_reconstruction(
        document, source, BoundaryRequest(equipment_id="vessel", expected_version=1)
    )
    assert len(model.connections) == 2
    assert len({edge.source_object_id for edge in model.connections}) == 2
    assert model.nodes[0].position != model.nodes[0].region.bbox[:2]
    request = ReconstructionRequest(
        equipment_id="vessel", expected_version=1, drawing=model, engineer_confirmed=True
    )
    result = create_reconstruction(document, request)
    exported = render_reconstruction(result, source)
    reader = PdfReader(io.BytesIO(exported))
    assert float(reader.pages[0].mediabox.width) == 1000  # New sheet, not a cropped viewport.
    contents = reader.pages[0].get_contents().get_data()
    assert b"6 2" in contents or b"6.0 2.0" in contents  # Source connection dash pattern retained.
    assert not reader.pages[0].images  # Not a screenshot or image collage.
    with pdfium.PdfDocument(exported) as pdf, closing(pdf[0]) as page:
        bitmap = page.render(scale=1)
        try:
            image = bitmap.to_pil().convert("RGB")
            assert min(low for low, _ in image.crop((98, 330, 103, 350)).getextrema()) > 250
            assert min(low for low, _ in image.crop((300, 330, 305, 350)).getextrema()) < 200
            banner = np.asarray(image.crop((30, 15, 780, 45)))
            assert ((banner[:, :, 0] > 120) & (banner[:, :, 1] < 80)).sum() > 10
        finally:
            bitmap.close()
        with closing(page.get_textpage()) as text:
            output_text = text.get_text_range()
            assert "SOURCE SPEC 10 bar" in output_text
            assert "SOURCE NOTES" in output_text
            assert "RECONSTRUCTED DRAFT" in output_text
            moved_lettering = text.get_text_bounded(300, 350, 460, 450)
            assert "V-101" in moved_lettering


def test_preview_is_not_engineer_approved_and_invalid_graphs_are_rejected():
    source, document = source_document()
    model = propose_reconstruction(
        document, source, BoundaryRequest(equipment_id="vessel", expected_version=1)
    )
    result = create_reconstruction(
        document,
        ReconstructionPreviewRequest(equipment_id="vessel", expected_version=1, drawing=model),
    )
    assert not result.engineer_confirmed
    assert result.status == "unconfirmed_preview"
    with pytest.raises(ValueError, match="checksum"):
        render_reconstruction(result, source + b"changed")
    payload = model.model_dump()
    payload["connections"][0]["target"] = "missing"
    with pytest.raises(ValueError, match="existing nodes"):
        ReconstructionModel.model_validate(payload)
    payload = model.model_dump()
    payload["nodes"].append(payload["nodes"][0])
    with pytest.raises(ValueError, match="unique"):
        ReconstructionModel.model_validate(payload)
    model.nodes = model.nodes[1:]
    model.connections = []
    with pytest.raises(ValueError, match="Selected equipment"):
        create_reconstruction(
            document,
            ReconstructionPreviewRequest(equipment_id="vessel", expected_version=1, drawing=model),
        )


def test_overlapping_regions_and_invalid_source_style_do_not_publish(tmp_path):
    source, document = source_document()
    model = propose_reconstruction(
        document, source, BoundaryRequest(equipment_id="vessel", expected_version=1)
    )
    duplicate = model.nodes[0].model_copy(update={"id": "duplicate"})
    model.nodes.append(duplicate)
    result = create_reconstruction(
        document,
        ReconstructionPreviewRequest(equipment_id="vessel", expected_version=1, drawing=model),
    )
    with pytest.raises(ValueError, match="Overlapping"):
        render_reconstruction(result, source)
    model.nodes.pop()
    model.connections[0].source_object_id = 99999
    with pytest.raises(ValueError, match="Invalid source vector"):
        render_reconstruction(result, source)


def test_api_confirmation_preview_and_persistence(tmp_path):
    source, document = source_document()
    with TestClient(create_app(tmp_path)) as client:
        doc = client.post(
            "/documents?filename=vessel.pdf&revision=A",
            content=source,
            headers={"Content-Type": "application/pdf"},
        ).json()
        base = f"/documents/{doc['id']}"
        doc = client.post(
            f"{base}/selections",
            json={
                "expected_version": 1,
                "tag": "V-101",
                "kind": "vessel",
                "location": document.evidence[-1].location.model_dump(),
            },
        ).json()
        equipment_id = doc["graph"]["equipment"][-1]["id"]
        model = client.post(
            f"{base}/reconstruction-proposals",
            json={"equipment_id": equipment_id, "expected_version": 2},
        ).json()
        inventory = client.get(f"{base}/source-objects?page=1").json()
        style = inventory[model["connections"][0]["source_object_id"]]["stroke"]
        assert style["dash"] == pytest.approx([6, 2])
        assert client.get(f"{base}/source-objects?page=99").status_code == 422
        payload = {"equipment_id": equipment_id, "expected_version": 2, "drawing": model}
        preview = client.post(f"{base}/reconstruction-preview", json=payload)
        assert preview.status_code == 200, preview.text[:200]
        assert preview.content.startswith(b"\x89PNG")
        assert client.post(f"{base}/reconstructions", json=payload).status_code == 422
        result = client.post(
            f"{base}/reconstructions", json={**payload, "engineer_confirmed": True}
        )
        assert result.status_code == 201, result.text
        record = result.json()
        response = client.get(f"/reconstructions/{record['id']}/drawing.pdf")
        assert response.status_code == 200
        assert response.content.startswith(b"%PDF-")
    with TestClient(create_app(tmp_path)) as client:
        assert client.get(f"/reconstructions/{record['id']}").json() == record


def test_existing_label_can_be_given_outline_without_duplicate_equipment(tmp_path):
    from pid_framework.store import Store

    source, document = source_document()
    # Model a confirmed label candidate without an engineer-defined symbol outline.
    equipment = document.graph.equipment[-1]
    document.evidence[-1].origin = "pdf_text"
    store = Store(tmp_path)
    store.save_source(document, source)
    store.save("document", document)
    with TestClient(create_app(tmp_path)) as client:
        base = f"/documents/{document.id}"
        request = {"equipment_id": equipment.id, "expected_version": 1}
        response = client.post(f"{base}/reconstruction-proposals", json=request)
        assert response.status_code == 422
        assert "outline first" in response.json()["detail"]
        update = client.post(
            f"{base}/selections",
            json={
                **request,
                "location": document.evidence[-1].location.model_dump(),
            },
        )
        assert update.status_code == 200, update.text
        assert len(update.json()["graph"]["equipment"]) == len(document.graph.equipment)
        assert update.json()["graph"]["equipment"][-1]["tag"] == equipment.tag
        request["expected_version"] = 2
        model = client.post(f"{base}/reconstruction-proposals", json=request)
        assert model.status_code == 200, model.text
        preview = client.post(
            f"{base}/reconstruction-preview",
            json={
                **request,
                "drawing": model.json(),
            },
        )
        assert preview.status_code == 200, preview.text[:200]
