import logging
from pathlib import Path
from uuid import UUID, uuid4

import pypdfium2 as pdfium
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from pid_framework.agents import Settings, review_with_azure
from pid_framework.models import (
    BoundaryProposal,
    BoundaryRequest,
    Document,
    Evidence,
    GraphUpdate,
    OriginalExtract,
    OriginalExtractRequest,
    Package,
    PackageRequest,
    ReconstructedDrawing,
    ReconstructionModel,
    ReconstructionPreviewRequest,
    ReconstructionRequest,
    Review,
    Selection,
)
from pid_framework.original import create_extract, propose_boundary, render_original_pdf
from pid_framework.packages import build_package, render_pdf, render_svg
from pid_framework.pdf import extract_document, render_page
from pid_framework.reconstruction import (
    create_reconstruction,
    propose_reconstruction,
    render_reconstruction,
    source_object_inventory,
)
from pid_framework.review import make_review
from pid_framework.store import ConflictError, Store

logger = logging.getLogger(__name__)
MAX_UPLOAD = 32 * 1024 * 1024


def create_app(data_root: Path = Path("data")) -> FastAPI:
    app = FastAPI(title="P&ID Engineering Workspace", version="0.3.0")
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
        return {"status": "ok", "model_calls_enabled": Settings().enable_model_calls}

    @app.get("/documents", response_model=list[Document])
    def documents():
        return store.documents()

    @app.post("/documents", response_model=Document, status_code=201)
    async def upload(
        request: Request,
        filename: str = Query(min_length=1, max_length=200),
        revision: str = Query(min_length=1, max_length=100),
    ):
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

    @app.put("/documents/{document_id}/graph", response_model=Document)
    def update_graph(document_id: UUID, update: GraphUpdate):
        def change(doc: Document) -> Document:
            doc.graph = update.graph
            return doc

        return store.update_document(str(document_id), update.expected_version, change)

    @app.post("/documents/{document_id}/selections", response_model=Document)
    def add_selection(document_id: UUID, selection: Selection):
        from pid_framework.models import Equipment

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
                existing.evidence_ids.append(evidence_id)
                existing.confirmed = True
                return doc
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

    @app.post("/documents/{document_id}/reviews", response_model=Review, status_code=201)
    async def review(document_id: UUID, use_agent: bool = False):
        doc = store.document(str(document_id))
        extra = []
        if use_agent:
            try:
                Settings().validate_access()
            except ValueError as exc:
                raise HTTPException(503, str(exc)) from exc
            extra = await review_with_azure(doc)
        result = make_review(doc, extra, use_agent)
        store.save("review", result)
        return result

    @app.get("/reviews/{review_id}", response_model=Review)
    def get_review(review_id: UUID):
        return Review.model_validate_json(store.get("review", str(review_id)))

    @app.post(
        "/documents/{document_id}/reconstruction-proposals", response_model=ReconstructionModel
    )
    def reconstruction_proposal(document_id: UUID, request: BoundaryRequest):
        doc = store.document(str(document_id))
        return propose_reconstruction(doc, store.source(doc), request)

    @app.get("/documents/{document_id}/source-objects")
    def drawing_source_objects(document_id: UUID, page: int = 1):
        doc = store.document(str(document_id))
        return source_object_inventory(store.source(doc), page)

    @app.post("/documents/{document_id}/reconstruction-preview")
    def reconstruction_preview(document_id: UUID, request: ReconstructionPreviewRequest):
        doc = store.document(str(document_id))
        result = create_reconstruction(doc, request)
        pdf = render_reconstruction(result, store.source(doc))
        return Response(render_page(pdf, 1), media_type="image/png")

    @app.post(
        "/documents/{document_id}/reconstructions",
        response_model=ReconstructedDrawing,
        status_code=201,
    )
    def reconstruct(document_id: UUID, request: ReconstructionRequest):
        doc = store.document(str(document_id))
        result = create_reconstruction(doc, request)
        render_reconstruction(result, store.source(doc))
        store.save("reconstruction", result)
        return result

    @app.get("/reconstructions/{drawing_id}", response_model=ReconstructedDrawing)
    def get_reconstruction(drawing_id: UUID):
        return ReconstructedDrawing.model_validate_json(
            store.get("reconstruction", str(drawing_id))
        )

    @app.get("/reconstructions/{drawing_id}/drawing.pdf")
    def reconstructed_pdf(drawing_id: UUID):
        result = ReconstructedDrawing.model_validate_json(
            store.get("reconstruction", str(drawing_id))
        )
        doc = store.document(result.document_id)
        return Response(
            render_reconstruction(result, store.source(doc)),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="reconstructed-pid-{result.id}.pdf"'
            },
        )

    @app.post("/documents/{document_id}/boundary-proposals", response_model=BoundaryProposal)
    def boundary_proposal(document_id: UUID, request: BoundaryRequest):
        doc = store.document(str(document_id))
        return propose_boundary(doc, store.source(doc), request)

    @app.post(
        "/documents/{document_id}/original-extracts",
        response_model=OriginalExtract,
        status_code=201,
    )
    def original_extract(document_id: UUID, request: OriginalExtractRequest):
        doc = store.document(str(document_id))
        result = create_extract(doc, store.source(doc), request)
        store.save("original_extract", result)
        return result

    @app.get("/original-extracts/{extract_id}", response_model=OriginalExtract)
    def get_original_extract(extract_id: UUID):
        return OriginalExtract.model_validate_json(store.get("original_extract", str(extract_id)))

    @app.get("/original-extracts/{extract_id}/drawing.pdf")
    def original_drawing(extract_id: UUID):
        result = OriginalExtract.model_validate_json(store.get("original_extract", str(extract_id)))
        doc = store.document(result.document_id)
        if result.source_sha256 != doc.sha256:
            raise ValueError("Source drawing checksum no longer matches extract")
        return Response(
            render_original_pdf(result, store.source(doc)),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="original-pid-{result.id}.pdf"'},
        )

    @app.post("/documents/{document_id}/packages", response_model=Package, status_code=201)
    def create_package(document_id: UUID, request: PackageRequest):
        doc = store.document(str(document_id))
        package = build_package(doc, request)
        if len(package.equipment) > 12:
            raise ValueError("Prototype export supports 12 entities; narrow the package boundary")
        store.save("package", package)
        return package

    @app.get("/packages/{package_id}", response_model=Package)
    def get_package(package_id: UUID):
        return Package.model_validate_json(store.get("package", str(package_id)))

    @app.get("/packages/{package_id}/drawing.{extension}")
    def drawing(package_id: UUID, extension: str):
        if extension not in ("svg", "pdf"):
            raise HTTPException(404, "Supported drawing formats are svg and pdf")
        package = Package.model_validate_json(store.get("package", str(package_id)))
        doc = store.document(package.document_id)
        renderer = render_svg if extension == "svg" else render_pdf
        media = "image/svg+xml" if extension == "svg" else "application/pdf"
        return Response(
            renderer(package, store.source(doc)),
            media_type=media,
            headers={
                "Content-Disposition": f'attachment; filename="equipment-{package.id}.{extension}"'
            },
        )

    return app


def app_factory() -> FastAPI:
    return create_app()
