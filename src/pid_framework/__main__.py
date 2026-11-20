import argparse
import json
from pathlib import Path

from pid_framework.pdf import extract_document
from pid_framework.store import Store


def main() -> None:
    parser = argparse.ArgumentParser(description="Local equipment P&ID generator")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8765)
    ingest = commands.add_parser("import-pdf")
    ingest.add_argument("path", type=Path)
    ingest.add_argument("--revision", required=True)
    ingest.add_argument("--data", type=Path, default=Path("data"))
    args = parser.parse_args()
    if args.command == "serve":
        import uvicorn

        uvicorn.run("pid_framework.api:app_factory", factory=True, host="127.0.0.1", port=args.port)
    else:
        content = args.path.read_bytes()
        if len(content) > 32 * 1024 * 1024 or not content.startswith(b"%PDF-"):
            parser.error("Input must be a PDF no larger than 32 MiB")
        document = extract_document(content, args.path.name, args.revision)
        store = Store(args.data)
        store.save_source(document, content)
        store.save("document", document)
        print(
            json.dumps(
                {
                    "document_id": document.id,
                    "pages": len(document.pages),
                    "equipment_candidates": len(document.graph.equipment),
                    "hold_occurrences": sum(e.id.startswith("hold-") for e in document.evidence),
                    "model_calls": False,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()