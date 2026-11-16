"""Create a clearly synthetic drawing, persisted package and example exports."""

import io
from pathlib import Path

from reportlab.pdfgen.canvas import Canvas

from pid_framework.models import (
    BoundaryRequest,
    EngineeringGraph,
    Evidence,
    Location,
    OriginalExtractRequest,
    PackageRequest,
)
from pid_framework.original import create_extract, propose_boundary, render_original_pdf
from pid_framework.packages import build_package, render_pdf, render_svg
from pid_framework.pdf import extract_document
from pid_framework.review import make_review
from pid_framework.store import Store


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    output = io.BytesIO()
    canvas = Canvas(output, pagesize=(800, 500))
    canvas.setFont("Helvetica-Bold", 18)
    canvas.drawString(40, 450, "SYNTHETIC DEMONSTRATION - NOT AN ENGINEERING DESIGN")
    canvas.setFont("Helvetica", 12)
    canvas.drawString(125, 300, "P-101")
    canvas.drawString(525, 300, "P-102")
    canvas.circle(145, 250, 28)
    canvas.circle(545, 250, 28)
    canvas.line(173, 250, 517, 250)
    canvas.drawString(250, 275, "Synthetic connection, no assigned size/class")
    canvas.drawString(
        40, 100, "HOLD 1: This demonstration has no approved equipment specifications."
    )
    canvas.save()
    source = output.getvalue()
    document = extract_document(source, "SYNTHETIC-DEMO.pdf", "DEMO")
    document.evidence.append(
        Evidence(
            id="demo-pump-region",
            location=Location(page=1, bbox=(0.12, 0.35, 0.23, 0.58)),
            text="Synthetic depicted pump and tag source region",
            origin="engineer_selection",
        )
    )
    document.graph.equipment[0].evidence_ids.append("demo-pump-region")
    graph = document.graph.model_dump()
    for equipment in graph["equipment"]:
        equipment["confirmed"] = True
    graph["connections"] = [
        {
            "id": "demo-connection",
            "source": "equipment-1",
            "target": "equipment-2",
            "description": "Synthetic connection between two demonstration symbols; no size/class",
            "evidence_ids": ["tag-1", "tag-2"],
            "confirmed": True,
        }
    ]
    document.graph = EngineeringGraph.model_validate(graph)
    store = Store(root / "data")
    store.save_source(document, source)
    store.save("document", document)
    review = make_review(document, [], False)
    store.save("review", review)
    package = build_package(
        document, PackageRequest(equipment_id="equipment-1", expected_version=1)
    )
    store.save("package", package)
    exports = root / "examples" / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    (exports / "synthetic-source.pdf").write_bytes(source)
    (exports / "synthetic-detail.svg").write_bytes(render_svg(package, source))
    (exports / "synthetic-detail.pdf").write_bytes(render_pdf(package, source))
    (exports / "synthetic-package.json").write_text(
        package.model_dump_json(indent=2), encoding="utf-8"
    )
    proposal = propose_boundary(
        document, source, BoundaryRequest(equipment_id="equipment-1", expected_version=1)
    )
    original = create_extract(
        document,
        source,
        OriginalExtractRequest(
            equipment_id="equipment-1",
            expected_version=1,
            regions=[
                proposal.region,
                Location(page=1, bbox=(0.02, 0.74, 0.98, 0.84)),
            ],
            engineer_confirmed=True,
        ),
    )
    store.save("original_extract", original)
    (exports / "synthetic-original-pid.pdf").write_bytes(render_original_pdf(original, source))
    (exports / "synthetic-original-provenance.json").write_text(
        original.model_dump_json(indent=2), encoding="utf-8"
    )
    print(f"Synthetic document: {document.id}")
    print(f"Draft examples: {exports}")


if __name__ == "__main__":
    main()
