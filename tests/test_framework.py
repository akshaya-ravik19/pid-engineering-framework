import io
from contextlib import closing

import httpx
import pypdfium2 as pdfium
import pytest
from agent_framework import Agent
from agent_framework.openai import OpenAIChatCompletionClient
from fastapi.testclient import TestClient
from openai import AsyncOpenAI
from reportlab.pdfgen.canvas import Canvas

from pid_framework.agents import GenerationAgents
from pid_framework.api import create_app
from pid_framework.models import (
    Document,
    EngineeringGraph,
    Finding,
    PackageRequest,
)
from pid_framework.packages import build_package, render_pdf, render_svg
from pid_framework.pdf import extract_document, render_page
from pid_framework.review import make_review


def drawing_bytes(text: bool = True) -> bytes:
    output = io.BytesIO()
    canvas = Canvas(output, pagesize=(600, 400))
    if text:
        canvas.drawString(100, 250, "P-101")
        canvas.drawString(350, 250, "P-102")
        canvas.drawString(100, 100, "HOLD 2")
    else:
        canvas.rect(100, 100, 50, 50)
    canvas.save()
    return output.getvalue()


@pytest.fixture
def document():
    return extract_document(drawing_bytes(), "synthetic.pdf", "A")


def test_extraction_coordinates_and_no_invented_graph(document):
    assert [e.tag for e in document.graph.equipment] == ["P-101", "P-102"]
    assert not any(e.confirmed for e in document.graph.equipment)
    assert document.graph.connections == []
    tag = document.evidence[0]
    assert tag.location.page == 1
    assert tag.location.bbox[0] == pytest.approx(100 / 600, abs=0.01)
    assert tag.location.bbox[1] == pytest.approx(0.35, abs=0.04)
    assert any(e.text == "HOLD 2" for e in document.evidence)
    assert render_page(drawing_bytes(), 1).startswith(b"\x89PNG")


def test_scanned_page_explicitly_reports_unsupported_ocr():
    document = extract_document(drawing_bytes(False), "scan.pdf", "A")
    assert not document.graph.equipment
    assert any("OCR is not implemented" in s for s in document.limitations)


def test_project_pump_tag_with_pair_suffix():
    output = io.BytesIO()
    canvas = Canvas(output)
    canvas.drawString(50, 700, "11-PBA673103 A/B")
    canvas.save()
    document = extract_document(output.getvalue(), "project-style.pdf", "A")
    assert [e.tag for e in document.graph.equipment] == ["11-PBA673103 A/B"]


def test_rotation_is_explicitly_rejected():
    output = io.BytesIO()
    canvas = Canvas(output)
    canvas.setPageRotation(90)
    canvas.drawString(50, 50, "P-101")
    canvas.save()
    with pytest.raises(ValueError, match="Rotated"):
        extract_document(output.getvalue(), "rotated.pdf", "A")


def test_invalid_evidence_cannot_be_saved(document):
    value = document.model_dump()
    value["graph"]["equipment"][0]["evidence_ids"] = ["missing"]
    with pytest.raises(ValueError, match="Unknown evidence"):
        Document.model_validate(value)
    with pytest.raises(ValueError, match="distinct existing"):
        EngineeringGraph.model_validate(
            {
                "equipment": [],
                "connections": [
                    {
                        "id": "edge",
                        "source": "a",
                        "target": "b",
                        "description": "test",
                        "evidence_ids": ["tag-1"],
                    }
                ],
            }
        )


def test_package_rejects_candidate_and_traverses_only_confirmed_edges(document):
    request = PackageRequest(equipment_id="equipment-1", expected_version=1)
    with pytest.raises(ValueError, match="Confirm"):
        build_package(document, request)
    for equipment in document.graph.equipment:
        equipment.confirmed = True
    graph = document.graph.model_dump()
    graph["connections"] = [
        {
            "id": "edge-1",
            "source": "equipment-1",
            "target": "equipment-2",
            "description": "Engineer-confirmed process connection",
            "evidence_ids": ["tag-1"],
            "confirmed": False,
        }
    ]
    document.graph = EngineeringGraph.model_validate(graph)
    assert len(build_package(document, request).equipment) == 1
    document.graph.connections[0].confirmed = True
    assert len(build_package(document, request).equipment) == 2
    request.boundary_ids = ["equipment-1"]
    assert len(build_package(document, request).equipment) == 1
    request.expected_version = 99
    with pytest.raises(ValueError, match="changed"):
        build_package(document, request)


def test_source_linked_exports_and_no_fabricated_specs(document):
    document.graph.equipment[0].confirmed = True
    package = build_package(
        document, PackageRequest(equipment_id="equipment-1", expected_version=1)
    )
    svg = render_svg(package, drawing_bytes()).decode()
    assert "NOT FOR CONSTRUCTION" in svg
    assert "No specifications entered" in svg
    assert "data:image/png;base64," in svg
    assert "No confirmed connections" in svg
    assert package.missing_information and package.proposals
    pdf = render_pdf(package, drawing_bytes())
    with pdfium.PdfDocument(pdf) as parsed:
        text = ""
        for i in range(len(parsed)):
            with closing(parsed[i]) as page, closing(page.get_textpage()) as tp:
                text += tp.get_text_range()
    assert "NOT FOR CONSTRUCTION" in text
    assert "P-101" in text
    assert "No specifications entered" in text


def test_findings_distinguish_queries_and_reject_unrelated_evidence(document):
    review = make_review(document, [], False)
    assert review.findings[0].category == "engineering_query"
    assert review.findings[0].rule_id == "QUERY-DOCUMENTED-HOLDS"
    assert review.limitations
    finding = Finding(
        rule_id="TEST",
        entity_ids=["equipment-1"],
        evidence_ids=["tag-2"],
        observation="Unsupported",
        requested_action="Verify",
        category="potential_technical_issue",
    )
    with pytest.raises(ValueError, match="unrelated"):
        make_review(document, [finding], True)


def test_full_api_persistence_selections_and_version_conflicts(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/app.js").status_code == 200
        response = client.post(
            "/documents?filename=synthetic.pdf&revision=A",
            content=drawing_bytes(),
            headers={"Content-Type": "application/pdf"},
        )
        assert response.status_code == 201, response.text
        doc = response.json()
        document_id = doc["id"]
        base = f"/documents/{document_id}"
        assert client.get(f"{base}/pages/1.png").content.startswith(b"\x89PNG")
        assert client.get(f"{base}/pages/9.png").status_code == 422
        # A manually selected region can supply evidence for equipment and its specifications.
        response = client.post(
            f"{base}/selections",
            json={
                "expected_version": 1,
                "tag": "P-103",
                "kind": "pump",
                "location": {"page": 1, "bbox": [0.1, 0.1, 0.5, 0.5]},
            },
        )
        assert response.status_code == 200, response.text
        doc = response.json()
        assert doc["version"] == 2
        assert doc["graph"]["equipment"][-1]["confirmed"]
        stale = client.post(
            f"{base}/selections",
            json={
                "expected_version": 1,
                "location": {"page": 1, "bbox": [0.1, 0.1, 0.5, 0.5]},
            },
        )
        assert stale.status_code == 409
        for route in ["reviews", "packages", "reconstruction-proposals", "original-extracts"]:
            assert client.post(f"{base}/{route}", json={}).status_code == 404
        assert client.put(f"{base}/graph", json={}).status_code == 404
    with TestClient(create_app(tmp_path)) as client:
        assert client.get(base).json() == doc


def test_invalid_pdf_cloud_disabled_and_cross_origin(tmp_path, monkeypatch):
    monkeypatch.setenv("PID_ENABLE_MODEL_CALLS", "false")
    with TestClient(create_app(tmp_path)) as client:
        assert (
            client.post(
                "/documents?filename=bad.pdf&revision=A",
                content=b"bad",
                headers={"Content-Type": "application/pdf"},
            ).status_code
            == 422
        )
        response = client.post(
            "/documents?filename=good.pdf&revision=A",
            content=drawing_bytes(),
            headers={"Content-Type": "application/pdf"},
        )
        document_id = response.json()["id"]
        assert (
            client.post(
                f"/documents/{document_id}/generations",
                json={"equipment_id": "equipment-1", "expected_version": 1, "use_agents": True},
            ).status_code
            == 503
        )
        denied = client.get("/health", headers={"Origin": "https://external.example"})
        assert denied.status_code == 403
        assert client.get("/documents/not-a-uuid").status_code == 422


def test_select_directly_from_drawing_without_entering_tag(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        doc = client.post(
            "/documents?filename=selection.pdf&revision=A",
            content=drawing_bytes(),
            headers={"Content-Type": "application/pdf"},
        ).json()
        base = f"/documents/{doc['id']}/selections"
        response = client.post(
            base,
            json={
                "expected_version": 1,
                "location": {"page": 1, "bbox": [0.15, 0.3, 0.3, 0.5]},
            },
        )
        assert response.status_code == 200, response.text
        selected = response.json()["graph"]["equipment"][-1]
        assert selected["tag"] == "P-101"
        assert selected["confirmed"]
        assert selected["kind"] == "equipment"
        response = client.post(
            base,
            json={
                "expected_version": 2,
                "location": {"page": 1, "bbox": [0.15, 0.3, 0.8, 0.5]},
            },
        )
        assert response.status_code == 200
        selected = response.json()["graph"]["equipment"][-1]
        assert selected["tag"].startswith("Selected equipment on page 1")
        assert "ambiguous" in response.json()["evidence"][-1]["text"]
        assert (
            client.post(
                base,
                json={
                    "expected_version": 3,
                    "location": {"page": 2, "bbox": [0.1, 0.1, 0.2, 0.2]},
                },
            ).status_code
            == 422
        )


async def test_real_microsoft_agent_sdk_with_mocked_model():
    from test_reconstruction import source_document

    from pid_framework.models import BoundaryRequest
    from pid_framework.reconstruction import propose_reconstruction

    source, document = source_document()
    drawing = propose_reconstruction(
        document,
        source,
        BoundaryRequest(equipment_id="vessel", expected_version=1),
        automatic=True,
    )
    evidence = [document.evidence[-1]]
    outputs = [
        {"evidence_ids": [evidence[0].id]},
        {"positions": [{"node_id": n.id, "x": n.position[0], "y": n.position[1]}
                       for n in drawing.nodes]},
        {
            "items": [
                {"evidence_id": evidence[0].id, "explanation": "Selected vessel source region."}
            ]
        },
    ]
    calls = []

    def completion(request: httpx.Request) -> httpx.Response:
        import json

        content = json.dumps(outputs[len(calls)])
        calls.append(json.loads(request.content))
        schema = calls[-1]["response_format"]["json_schema"]["schema"]
        def check_arrays(value):
            if isinstance(value, dict):
                if value.get("type") == "array":
                    assert "items" in value
                    assert "prefixItems" not in value
                for child in value.values():
                    check_arrays(child)
            elif isinstance(value, list):
                for child in value:
                    check_arrays(child)
        check_arrays(schema)
        return httpx.Response(
            200,
            json={
                "id": "offline-test",
                "object": "chat.completion",
                "created": 0,
                "model": "offline",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(completion)) as http:
        async with AsyncOpenAI(api_key="offline-placeholder", http_client=http) as sdk:
            client = OpenAIChatCompletionClient(model="offline", async_client=sdk)
            agents = GenerationAgents(*(Agent(client=client) for _ in range(3)))
            planned, context, explanation = await agents.run("V-101", evidence, drawing)
    assert len(calls) == 3
    assert planned.connections == drawing.connections
    assert [n.position for n in planned.nodes] == [n.position for n in drawing.nodes]
    assert context == evidence
    assert explanation.items[0].evidence_id == evidence[0].id
    assert all(call["response_format"]["type"] == "json_schema" for call in calls)
