import io
from contextlib import closing

import numpy as np
import pypdfium2 as pdfium
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from reportlab.pdfgen.canvas import Canvas

from pid_framework.api import create_app
from pid_framework.models import (
    BoundaryRequest,
    Location,
    OriginalExtractRequest,
)
from pid_framework.original import create_extract, propose_boundary, render_original_pdf
from pid_framework.pdf import extract_document
from pid_framework.store import ConflictError


def source_drawing() -> bytes:
    output = io.BytesIO()
    canvas = Canvas(output, pagesize=(600, 400))
    canvas.setStrokeColorRGB(0.1, 0.3, 0.7)
    canvas.setLineWidth(1)
    canvas.circle(130, 200, 20)
    canvas.circle(400, 200, 20)
    canvas.line(150, 200, 380, 200)
    canvas.line(280, 200, 280, 280)
    canvas.circle(280, 295, 15)
    canvas.setFont("Helvetica", 11)
    canvas.drawString(110, 160, "P-101")
    canvas.drawString(380, 160, "P-102")
    canvas.drawString(260, 320, "PT-101")
    canvas.drawString(50, 40, "SOURCE NOTE: pressure 10 bar; original lettering")
    canvas.line(520, 80, 570, 80)
    canvas.save()
    return output.getvalue()


def confirmed_document(source: bytes):
    document = extract_document(source, "source.pdf", "A")
    first = document.graph.equipment[0]
    first.confirmed = True
    evidence = next(e for e in document.evidence if e.id in first.evidence_ids)
    evidence.location = Location(page=1, bbox=(0.17, 0.43, 0.26, 0.62))
    return document


def render_at_scale(source: bytes, page_index: int = 0, crop=(0, 0, 0, 0)):
    with pdfium.PdfDocument(source) as pdf, closing(pdf[page_index]) as page:
        bitmap = page.render(scale=1, crop=crop)
        try:
            return np.array(bitmap.to_pil().convert("RGB"))
        finally:
            bitmap.close()


def test_boundary_follows_connected_pipe_and_instrument_not_distant_note():
    source = source_drawing()
    document = confirmed_document(source)
    proposal = propose_boundary(
        document, source, BoundaryRequest(equipment_id="equipment-1", expected_version=1)
    )
    x1, y1, x2, y2 = proposal.region.bbox
    assert x1 < 110 / 600
    assert x2 > 420 / 600
    assert y1 < 90 / 400  # Connected instrument top.
    assert y2 > 240 / 400  # Equipment tag seed.
    assert x2 < 500 / 600  # Disconnected distant line is not followed.
    assert y2 < 350 / 400  # Detached specification note needs a separate region.
    assert proposal.connected_components >= 1
    assert any("Crossing" in warning for warning in proposal.warnings)


def test_trace_rejects_unconfirmed_equipment_and_missing_linework():
    source = source_drawing()
    document = extract_document(source, "source.pdf", "A")
    request = BoundaryRequest(equipment_id="equipment-1", expected_version=1)
    with pytest.raises(ValueError, match="depicted"):
        propose_boundary(document, source, request)
    document.graph.equipment[0].confirmed = True
    evidence = document.evidence[0]
    evidence.location = Location(page=1, bbox=(0.02, 0.02, 0.04, 0.04))
    with pytest.raises(ValueError, match="No connected linework"):
        propose_boundary(document, source, request)


def test_original_export_preserves_vectors_text_style_scale_and_pixels():
    source = source_drawing()
    document = confirmed_document(source)
    regions = [
        Location(page=1, bbox=(0.1, 0.1, 0.8, 0.8)),
        Location(page=1, bbox=(0.05, 0.85, 0.95, 0.95)),
    ]
    result = create_extract(
        document,
        source,
        OriginalExtractRequest(
            equipment_id="equipment-1",
            expected_version=1,
            regions=regions,
            engineer_confirmed=True,
        ),
    )
    output = render_original_pdf(result, source)
    original_page = PdfReader(io.BytesIO(source)).pages[0]
    exported = PdfReader(io.BytesIO(output))
    assert len(exported.pages) == 2
    assert exported.pages[0].get_contents().get_data() == original_page.get_contents().get_data()
    assert exported.pages[1].get_contents().get_data() == original_page.get_contents().get_data()
    assert float(exported.pages[0].mediabox.width) == pytest.approx(420)
    assert float(exported.pages[0].mediabox.height) == pytest.approx(280)
    assert float(exported.pages[1].mediabox.height) == pytest.approx(40)
    resources = exported.pages[0]["/Resources"]
    assert all(
        obj.get_object().get("/Subtype") != "/Image"
        for obj in resources.get("/XObject", {}).values()
    )
    assert "pressure 10 bar" in result.extracted_text[1].text
    assert "pressure 10 bar" not in result.extracted_text[0].text
    original_pixels = render_at_scale(source, crop=(60, 80, 120, 40))
    exported_pixels = render_at_scale(output)
    assert original_pixels.shape == exported_pixels.shape
    assert np.abs(original_pixels.astype(float) - exported_pixels).mean() < 0.1
    note_pixels = render_at_scale(source, crop=(30, 20, 30, 340))
    assert np.abs(note_pixels.astype(float) - render_at_scale(output, 1)).mean() < 0.1
    with pytest.raises(ValueError, match="checksum"):
        render_original_pdf(result, source + b"changed")


def test_nonzero_source_cropbox_mapping_preserves_visual_crop():
    writer = PdfWriter()
    page = writer.add_page(PdfReader(io.BytesIO(source_drawing())).pages[0])
    page.cropbox = RectangleObject((50, 40, 550, 360))
    buffer = io.BytesIO()
    writer.write(buffer)
    source = buffer.getvalue()
    document = confirmed_document(source)
    result = create_extract(
        document,
        source,
        OriginalExtractRequest(
            equipment_id="equipment-1",
            expected_version=1,
            regions=[Location(page=1, bbox=(0, 0, 1, 1))],
            engineer_confirmed=True,
        ),
    )
    output = render_original_pdf(result, source)
    assert np.array_equal(render_at_scale(source), render_at_scale(output))


def test_crop_and_boundary_exports_are_no_longer_exposed(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        doc = client.post(
            "/documents?filename=source.pdf&revision=A",
            content=source_drawing(),
            headers={"Content-Type": "application/pdf"},
        ).json()
        base = f"/documents/{doc['id']}"
        request = {"equipment_id": "equipment-1", "expected_version": 1}
        assert client.post(f"{base}/boundary-proposals", json=request).status_code == 404
        assert client.post(f"{base}/original-extracts", json=request).status_code == 404


def test_extract_requires_selected_source_region_and_current_version():
    source = source_drawing()
    document = confirmed_document(source)
    request = OriginalExtractRequest(
        equipment_id="equipment-1",
        expected_version=1,
        regions=[Location(page=1, bbox=(0.8, 0.8, 0.9, 0.9))],
        engineer_confirmed=True,
    )
    with pytest.raises(ValueError, match="Primary crop"):
        create_extract(document, source, request)
    request.expected_version = 99
    with pytest.raises(ConflictError):
        create_extract(document, source, request)
