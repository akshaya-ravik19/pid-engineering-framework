import io
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas
from test_reconstruction import source_document

from pid_framework.agents import AgentDrawingLayout, GenerationAgents, validate_layout
from pid_framework.api import create_app
from pid_framework.models import (
    BoundaryRequest,
    DrawingLayout,
    EvidenceContext,
    GenerationExplanation,
    NodePosition,
)
from pid_framework.reconstruction import propose_reconstruction
from pid_framework.store import Store


def test_upload_requires_pdf_and_ui_has_only_generation_workflow(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        page = client.get("/").text
        assert 'id="file"' in page and 'id="generate"' in page
        for removed in [
            'id="documents"',
            'id="review"',
            'id="reconstructionModel"',
            'id="exportOriginal"',
        ]:
            assert removed not in page
        script = client.get("/app.js").text
        assert 'api("/documents")' not in script
        assert client.get("/documents").json() == []
        assert (
            client.post(
                "/documents?filename=drawing.png",
                content=b"%PDF-pretend",
                headers={"Content-Type": "application/pdf"},
            ).status_code
            == 415
        )


def test_symbol_only_draft_is_explicit_and_never_approved(tmp_path):
    output = io.BytesIO()
    canvas = Canvas(output, pagesize=(600, 400))
    canvas.circle(130, 200, 20)
    canvas.drawString(110, 195, "P-101")
    canvas.save()
    with TestClient(create_app(tmp_path)) as client:
        document = client.post(
            "/documents?filename=symbol.pdf",
            content=output.getvalue(),
            headers={"Content-Type": "application/pdf"},
        ).json()
        base = f"/documents/{document['id']}"
        document = client.post(
            f"{base}/selections",
            json={
                "expected_version": 1,
                "location": {"page": 1, "bbox": [0.18, 0.44, 0.26, 0.56]},
            },
        ).json()
        equipment = document["graph"]["equipment"][-1]
        result = client.post(
            f"{base}/generations",
            json={"equipment_id": equipment["id"], "expected_version": 2},
        )
        assert result.status_code == 201, result.text
        saved = result.json()
        assert not saved["reconstruction"]["engineer_confirmed"]
        assert saved["reconstruction"]["drawing"]["connections"] == []
        assert any("not a complete P&ID" in text for text in saved["limitations"])
        pdf = client.get(f"/generations/{saved['id']}/drawing.pdf").content
        parsed = PdfReader(io.BytesIO(pdf))
        assert not parsed.pages[0].images
        assert float(parsed.pages[0].mediabox.width) == 600
        assert "NOT FOR CONSTRUCTION" in parsed.pages[0].extract_text()
        review_url = f"/generations/{saved['id']}/reviews"
        review = {
            "expected_version": 1,
            "reviewer": "Prototype reviewer",
            "decision": "accepted",
            "comment": "Compared source and draft; limitations recorded.",
        }
        assert client.post(review_url, json=review).status_code == 422
        review["checked_source_and_limitations"] = True
        accepted = client.post(review_url, json=review)
        assert accepted.status_code == 200
        assert accepted.json()["review_status"] == "accepted"
        assert client.post(review_url, json=review).status_code == 409
        review.update(
            expected_version=2, decision="changes_requested", comment="Recheck connections."
        )
        changed = client.post(review_url, json=review).json()
        assert changed["review_status"] == "changes_requested"
        assert len(changed["review_history"]) == 2
        persisted = client.get(f"/generations/{saved['id']}").json()
        assert persisted["review_history"] == changed["review_history"]
        reviewed_pdf = client.get(f"/generations/{saved['id']}/drawing.pdf")
        report = PdfReader(io.BytesIO(reviewed_pdf.content))
        text = "".join(page.extract_text() for page in report.pages)
        assert "changes_requested" in text
        assert "Prototype reviewer" in text
        assert "NOT FOR CONSTRUCTION" in text
        client.post(
            f"{base}/selections",
            json={
                "expected_version": 2,
                "location": {"page": 1, "bbox": [0.18, 0.44, 0.26, 0.56]},
            },
        ).raise_for_status()
        review.update(expected_version=3, decision="accepted")
        assert client.post(review_url, json=review).status_code == 409


def test_reselection_uses_latest_outline(tmp_path):
    source, document = source_document()
    store = Store(tmp_path)
    store.save_source(document, source)
    store.save("document", document)
    equipment = document.graph.equipment[-1]
    with TestClient(create_app(tmp_path)) as client:
        region = document.evidence[-1].location.model_dump()
        region["bbox"] = [0.097, 0.368, 0.223, 0.746]
        response = client.post(
            f"/documents/{document.id}/selections",
            json={
                "equipment_id": equipment.id,
                "expected_version": 1,
                "location": region,
            },
        )
        assert response.status_code == 200
        saved = response.json()
        selected = saved["graph"]["equipment"][-1]
        assert "vessel-symbol" not in selected["evidence_ids"]
        refs = {e["id"]: e for e in saved["evidence"]}
        assert refs[selected["evidence_ids"][-1]]["location"] == region


def test_layout_rejects_missing_nodes_overlap_and_outside_sheet():
    source, document = source_document()
    drawing = propose_reconstruction(
        document,
        source,
        BoundaryRequest(equipment_id="vessel", expected_version=1),
        automatic=True,
    )
    positions = [NodePosition(node_id=n.id, position=n.position) for n in drawing.nodes]
    with pytest.raises(ValueError, match="exactly once"):
        validate_layout(drawing, DrawingLayout(positions=positions[:-1]))
    positions[-1] = NodePosition(node_id=positions[-1].node_id, position=(0.99, 0.99))
    with pytest.raises(ValueError, match="outside"):
        validate_layout(drawing, DrawingLayout(positions=positions))
    positions = [NodePosition(node_id=n.id, position=(0.32, 0.24)) for n in drawing.nodes]
    with pytest.raises(ValueError, match="overlapping"):
        validate_layout(drawing, DrawingLayout(positions=positions))


@pytest.mark.parametrize("bad_stage", ["evidence", "explanation"])
async def test_agents_reject_invented_citations(bad_stage):
    from types import SimpleNamespace

    source, document = source_document()
    drawing = propose_reconstruction(
        document,
        source,
        BoundaryRequest(equipment_id="vessel", expected_version=1),
        automatic=True,
    )
    evidence = [document.evidence[-1]]
    context = EvidenceContext(
        evidence_ids=["invented" if bad_stage == "evidence" else evidence[0].id]
    )
    layout = AgentDrawingLayout(
        positions=[{"node_id": n.id, "x": n.position[0], "y": n.position[1]} for n in drawing.nodes]
    )
    explanation = GenerationExplanation(
        items=[{"evidence_id": "invented", "explanation": "Unsupported source claim"}]
    )
    agents = [
        AsyncMock(run=AsyncMock(return_value=SimpleNamespace(text=value.model_dump_json())))
        for value in [context, layout, explanation]
    ]
    with pytest.raises(ValueError, match="source references|outside the selected"):
        await GenerationAgents(*agents).run("V-101", evidence, drawing)


def test_agent_error_does_not_save_or_fall_back(tmp_path, monkeypatch):
    source, document = source_document()
    store = Store(tmp_path)
    store.save_source(document, source)
    store.save("document", document)
    monkeypatch.setattr("pid_framework.api.Settings.validate_access", lambda self: None)
    mock = AsyncMock(side_effect=ValueError("Invalid source evidence from agent"))
    monkeypatch.setattr("pid_framework.generation.generate_with_azure", mock)
    with TestClient(create_app(tmp_path)) as client:
        response = client.post(
            f"/documents/{document.id}/generations",
            json={"equipment_id": "vessel", "expected_version": 1, "use_agents": True},
        )
        assert response.status_code == 422
        assert "Invalid source evidence" in response.json()["detail"]
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM records WHERE kind='generation'").fetchone()[0] == 0
