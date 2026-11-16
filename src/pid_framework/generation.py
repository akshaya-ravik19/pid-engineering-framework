import io
from contextlib import closing
from html import escape
from uuid import uuid4

import pypdfium2 as pdfium
from pypdf import PdfReader, PdfWriter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from starlette.concurrency import run_in_threadpool

from pid_framework.agents import (
    AGENT_NAMES,
    Settings,
    generate_with_azure,
    validate_layout,
)
from pid_framework.connected import connected_drawing
from pid_framework.models import (
    Document,
    DrawingLayout,
    Evidence,
    GeneratedDrawing,
    GenerationRequest,
    NodePosition,
    ReconstructionModel,
    ReconstructionPreviewRequest,
)
from pid_framework.original import pdf_coordinates, selected_equipment
from pid_framework.pdf import PDF_LOCK
from pid_framework.reconstruction import (
    create_reconstruction,
    render_reconstruction,
)


def drawing_text_evidence(source: bytes, drawing: ReconstructionModel) -> list[Evidence]:
    records = []
    with PDF_LOCK, pdfium.PdfDocument(source) as pdf:
        with closing(pdf[drawing.page - 1]) as page, closing(page.get_textpage()) as textpage:
            for node in drawing.nodes:
                text = textpage.get_text_bounded(*pdf_coordinates(page, node.region)).strip()
                for offset in range(0, len(text), 4000):
                    records.append(
                        Evidence(
                            id=f"drawing-source-{node.id}-{offset // 4000}",
                            location=node.region,
                            text=text[offset : offset + 4000],
                        )
                    )
    return records


async def generate_drawing(
    document: Document, source: bytes, request: GenerationRequest
) -> GeneratedDrawing:
    equipment = selected_equipment(document, request)
    drawing = await run_in_threadpool(connected_drawing, document, source, request)
    evidence = [
        item
        for item in document.evidence
        if item.location.page == drawing.page
        and (
            item.id in equipment.evidence_ids
            or any(
                item.location.bbox[0] >= node.region.bbox[0] - 0.001
                and item.location.bbox[1] >= node.region.bbox[1] - 0.001
                and item.location.bbox[2] <= node.region.bbox[2] + 0.001
                and item.location.bbox[3] <= node.region.bbox[3] + 0.001
                for node in drawing.nodes
            )
        )
    ]
    evidence.extend(await run_in_threadpool(drawing_text_evidence, source, drawing))
    explanation = None
    if request.use_agents:
        drawing, context, explanation = await generate_with_azure(equipment.tag, evidence, drawing)
        # Always retain all deterministic source references, even if an agent selects fewer.
        evidence = list({item.id: item for item in evidence + context}.values())
    drawing = validate_layout(
        drawing,
        DrawingLayout(
            positions=[
                NodePosition(node_id=node.id, position=node.position) for node in drawing.nodes
            ]
        ),
    )
    reconstruction = create_reconstruction(
        document,
        ReconstructionPreviewRequest(
            equipment_id=equipment.id, expected_version=document.version, drawing=drawing
        ),
    )
    reconstruction.status = "generated_draft_not_for_construction"
    result = GeneratedDrawing(
        id=str(uuid4()),
        document_id=document.id,
        document_version=document.version,
        equipment_tag=equipment.tag,
        reconstruction=reconstruction,
        summary=(
            f"New connected-system P&ID draft for {equipment.tag}, source page {drawing.page}: "
            f"{len(drawing.nodes)} independently positioned source elements and "
            f"{len(drawing.connections)} traced piping/connection segments. "
            "Original vector symbols and lettering are reused on a new sheet; "
            "supporting source symbols and traceable same-page pipelines are included "
            "and rerouted with increased spacing. Off-page continuations are not followed. "
            "This is not a cropped source page. "
            "The original uploaded drawing is unchanged."
        ),
        limitations=list(dict.fromkeys(drawing.warnings + document.limitations)),
        source_evidence=evidence,
        agent_explanation=explanation,
        agents_used=AGENT_NAMES if request.use_agents else [],
        model_deployment=Settings().azure_deployment if request.use_agents else None,
    )
    # Fail before saving a success-shaped record if native rendering is unsupported.
    await run_in_threadpool(render_generated_pdf, result, source)
    return result


def render_generated_pdf(result: GeneratedDrawing, source: bytes) -> bytes:
    drawing = render_reconstruction(result.reconstruction, source)
    report = io.BytesIO()
    styles = getSampleStyleSheet()
    story = []

    def paragraph(text: str, style: str = "BodyText") -> None:
        story.append(Paragraph(escape(text), styles[style]))
        story.append(Spacer(1, 8))

    paragraph(f"Equipment P&ID: {result.equipment_tag}", "Title")
    paragraph("DRAFT - NOT FOR CONSTRUCTION OR DESIGN APPROVAL", "Heading2")
    paragraph(result.summary)
    paragraph("How to read this drawing", "Heading2")
    paragraph(
        "The first sheet shows the selected equipment and its traceable connected system "
        "on the selected source page. Supporting symbols are independently positioned with "
        "increased spacing, and pipelines are rerouted. Symbols retain the source scale. "
        "Linework represents geometric connectivity, not verified process connectivity. "
        "A missing item must not be interpreted as absent from the actual installation."
    )
    paragraph("Source and traceability", "Heading2")
    paragraph(
        f"Document {result.document_id}; version {result.document_version}; "
        f"source revision {result.reconstruction.revision}; "
        f"source SHA256 {result.reconstruction.source_sha256}"
    )
    for item in result.source_evidence:
        paragraph(f"{item.id} | source page {item.location.page} | {item.text}")
    boundaries = [
        node for node in result.reconstruction.drawing.nodes if node.kind == "continuation_boundary"
    ]
    if boundaries:
        paragraph("Same-page boundaries and continuations", "Heading2")
        for node in boundaries:
            paragraph(node.label)
    if result.agent_explanation is not None:
        paragraph("AI-assisted explanations (not independently verified)", "Heading2")
        for item in result.agent_explanation.items:
            paragraph(f"{item.evidence_id}: {item.explanation}")
    paragraph("Coverage and uncertainty", "Heading2")
    for limitation in result.limitations:
        paragraph(limitation)
    paragraph(
        "Generation mode: "
        + (
            "Microsoft Agent Framework: "
            + ", ".join(result.agents_used)
            + f"; deployment {result.model_deployment}."
            if result.agents_used
            else "local deterministic processing; no AI agents were invoked."
        )
    )
    SimpleDocTemplate(report).build(story)
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(drawing)))
    writer.append(PdfReader(io.BytesIO(report.getvalue())))
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()
