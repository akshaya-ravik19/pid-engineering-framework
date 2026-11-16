"""Generate a synthetic source and an explicitly unconfirmed, newly laid-out interface sheet."""

import io
from pathlib import Path

from reportlab.pdfgen.canvas import Canvas

from pid_framework.models import (
    BoundaryRequest,
    Equipment,
    Evidence,
    Location,
    ReconstructionPreviewRequest,
)
from pid_framework.pdf import extract_document
from pid_framework.reconstruction import (
    create_reconstruction,
    propose_reconstruction,
    render_reconstruction,
)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    exports = root / "examples" / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=(1000, 700))
    canvas.setFont("Helvetica", 11)
    canvas.drawString(40, 650, "SYNTHETIC SOURCE - NOT AN ENGINEERING DESIGN")
    canvas.setStrokeColorRGB(0.1, 0.3, 0.7)
    canvas.setLineWidth(0.8)
    canvas.rect(100, 180, 120, 260)
    canvas.drawString(105, 300, "DEMO HPU")
    for y, value in ((360, "DEMO DUTY A"), (260, "DEMO DUTY B")):
        canvas.setDash([6, 2])
        canvas.line(220, y, 480, y)
        canvas.setDash()
        canvas.rect(450, y, 40, 20)
        canvas.drawString(300, y + 12, value)
    canvas.line(780, 20, 780, 680)
    canvas.drawString(800, 200, "SYNTHETIC NOTES")
    canvas.drawString(800, 180, "NO DESIGN VALUES ASSIGNED")
    canvas.save()
    source = buffer.getvalue()
    document = extract_document(source, "synthetic-interface-source.pdf", "DEMO")
    region = Location(page=1, bbox=(0.098, 0.369, 0.222, 0.745))
    document.evidence.append(
        Evidence(
            id="demo-symbol",
            location=region,
            text="Synthetic package outline",
            origin="engineer_selection",
        )
    )
    document.graph.equipment.append(
        Equipment(
            id="demo-hpu",
            tag="Synthetic HPU",
            kind="package",
            confirmed=True,
            evidence_ids=["demo-symbol"],
        )
    )
    proposal = propose_reconstruction(
        document, source, BoundaryRequest(equipment_id="demo-hpu", expected_version=1)
    )
    result = create_reconstruction(
        document,
        ReconstructionPreviewRequest(equipment_id="demo-hpu", expected_version=1, drawing=proposal),
    )
    (exports / "reconstruction-source.pdf").write_bytes(source)
    (exports / "reconstructed-interface-preview.pdf").write_bytes(
        render_reconstruction(result, source)
    )
    (exports / "reconstructed-interface-model.json").write_text(
        result.model_dump_json(indent=2), encoding="utf-8"
    )
    print(f"Unconfirmed synthetic reconstruction: {exports}")


if __name__ == "__main__":
    main()
