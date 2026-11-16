import logging
from pathlib import Path
from uuid import UUID, uuid4

import pypdfium2 as pdfium
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from pid_framework.agents import Settings
from pid_framework.generation import generate_drawing, render_generated_pdf
from pid_framework.models import (
    Document,
    Equipment,
    Evidence,
    GeneratedDrawing,
    GenerationRequest,
    Selection,
)
from pid_framework.pdf import extract_document, render_page
from pid_framework.store import ConflictError, Store

logger = logging.getLogger(__name__)
MAX_UPLOAD = 32 * 1024 * 1024


def create_app(data_root: Path = Path("data")) -> FastAPI:
    app = FastAPI(title="Equipment P&ID Generator", version="0.3.0")
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
    )
    store = Store(data_root)

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        origin = request.headers.get("origin")
        allowed = {
            f"http://{request.headers.get('host')}",
            f"https://{request.headers.get('host')}",
        }
        if origin and origin not in allowed:
            return JSONResponse(status_code=403, content={"detail": "Cross-origin access denied"})
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; object-src 'none'; frame-ancestors 'none'"
        )
        return response

    @app.exception_handler(KeyError)
    async def not_found(request: Request, exc: KeyError):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ConflictError)
    async def conflict(request: Request, exc: ConflictError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def invalid(request: Request, exc: ValueError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(Exception)
    async def failed(request: Request, exc: Exception):
        logger.exception("Operation failed: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500, content={"detail": "Operation failed. Consult local server logs."}
        )

    @app.get("/")
    def home():
        return FileResponse(Path(__file__).with_name("web") / "index.html")

    @app.get("/app.js")
    def script():
        return FileResponse(Path(__file__).with_name("web") / "app.js")

    @app.get("/health")
    def health():
        settings = Settings()
        return {
            "status": "ok",
            "model_calls_enabled": settings.enable_model_calls,
            "agents_configured": bool(
                settings.enable_model_calls
                and settings.azure_endpoint
                and settings.azure_deployment
            ),
        }

    @app.get("/documents", response_model=list[Document])
    def documents():
        return store.documents()

    @app.post("/documents", response_model=Document, status_code=201)
    async def upload(
        request: Request,
        filename: str = Query(min_length=1, max_length=200),
        revision: str = Query(default="UNVERIFIED", min_length=1, max_length=100),
    ):
        if not filename.lower().endswith(".pdf"):
            raise HTTPException(415, "Only P&ID PDF files can be uploaded")
        if request.headers.get("content-type", "").split(";")[0] != "application/pdf":
            raise HTTPException(415, "Send raw PDF bytes with Content-Type: application/pdf")
        chunks = bytearray()
        async for chunk in request.stream():
            chunks.extend(chunk)
            if len(chunks) > MAX_UPLOAD:
                raise HTTPException(413, "Maximum PDF size is 32 MiB")
        content = bytes(chunks)
        if not content.startswith(b"%PDF-"):
            raise HTTPException(422, "File does not have a PDF header")
        try:
            document = await run_in_threadpool(extract_document, content, filename, revision)
        except pdfium.PdfiumError as exc:
            raise HTTPException(422, f"PDF cannot be opened: {exc}") from exc
        await run_in_threadpool(store.save_source, document, content)
        store.save("document", document)
        return document

    @app.get("/documents/{document_id}", response_model=Document)
    def document(document_id: UUID):
        return store.document(str(document_id))

    @app.get("/documents/{document_id}/source")
    def source(document_id: UUID):
        doc = store.document(str(document_id))
        return Response(store.source(doc), media_type="application/pdf")

    @app.get("/documents/{document_id}/pages/{page}.png")
    def page_image(document_id: UUID, page: int):
        doc = store.document(str(document_id))
        return Response(render_page(store.source(doc), page), media_type="image/png")

    @app.post("/documents/{document_id}/selections", response_model=Document)
    def add_selection(document_id: UUID, selection: Selection):
        def change(doc: Document) -> Document:
            if selection.location.page > len(doc.pages):
                raise ValueError("Selection page is outside document")
            x1, y1, x2, y2 = selection.location.bbox
            existing = None
            if selection.equipment_id:
                existing = next(
                    (item for item in doc.graph.equipment if item.id == selection.equipment_id),
                    None,
                )
                if existing is None:
                    raise ValueError("Selected component no longer exists")
            candidates = [
                item
                for item in doc.evidence
                if item.origin == "pdf_text"
                and item.location.page == selection.location.page
                and x1 <= item.location.bbox[0]
                and y1 <= item.location.bbox[1]
                and x2 >= item.location.bbox[2]
                and y2 >= item.location.bbox[3]
                and any(item.id in equipment.evidence_ids for equipment in doc.graph.equipment)
            ]
            tag = (
                selection.tag
                or (existing.tag if existing else None)
                or (
                    candidates[0].text
                    if len(candidates) == 1
                    else f"Selected equipment on page {selection.location.page} "
                    f"({len(doc.graph.equipment) + 1})"
                )
            )
            evidence_id = f"selection-{uuid4()}"
            doc.evidence.append(
                Evidence(
                    id=evidence_id,
                    location=selection.location,
                    text=(
                        f"Engineer-selected source region for {tag}. "
                        + (
                            "Source label is ambiguous or unavailable; region identifies equipment."
                            if not selection.tag and len(candidates) != 1
                            else ""
                        )
                    ),
                    origin="engineer_selection",
                )
            )
            if existing:
                # The latest user selection supersedes earlier outline selections.
                prior = {e.id for e in doc.evidence if e.origin == "engineer_selection"}
                existing.evidence_ids = [ref for ref in existing.evidence_ids if ref not in prior]
                existing.evidence_ids.append(evidence_id)
                existing.confirmed = True
            else:
                doc.graph.equipment.append(
                    Equipment(
                        id=f"equipment-{uuid4()}",
                        tag=tag,
                        kind=selection.kind,
                        evidence_ids=[evidence_id],
                        confirmed=True,
                    )
                )
            return doc

        return store.update_document(str(document_id), selection.expected_version, change)

    @app.post(
        "/documents/{document_id}/generations", response_model=GeneratedDrawing, status_code=201
    )
    async def generate(document_id: UUID, request: GenerationRequest):
        if request.use_agents:
            try:
                Settings().validate_access()
            except ValueError as exc:
                raise HTTPException(503, str(exc)) from exc
        doc = store.document(str(document_id))
        result = await generate_drawing(doc, store.source(doc), request)
        store.save("generation", result)
        return result

    def saved_generation(drawing_id: UUID) -> GeneratedDrawing:
        return GeneratedDrawing.model_validate_json(store.get("generation", str(drawing_id)))

    @app.get("/generations/{drawing_id}", response_model=GeneratedDrawing)
    def get_generation(drawing_id: UUID):
        return saved_generation(drawing_id)

    @app.get("/generations/{drawing_id}/drawing.pdf")
    def generated_pdf(drawing_id: UUID):
        result = saved_generation(drawing_id)
        doc = store.document(result.document_id)
        return Response(
            render_generated_pdf(result, store.source(doc)),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="equipment-pid-{result.id}.pdf"'
            },
        )

    @app.get("/generations/{drawing_id}/preview.png")
    def generated_preview(drawing_id: UUID):
        result = saved_generation(drawing_id)
        doc = store.document(result.document_id)
        pdf = render_generated_pdf(result, store.source(doc))
        return Response(render_page(pdf, 1), media_type="image/png")

    return app


def app_factory() -> FastAPI:
    return create_app()
