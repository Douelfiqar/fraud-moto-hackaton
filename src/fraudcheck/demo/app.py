"""The HTTP routes of the control desk.

The page is static (``static/``); everything it shows comes from the routes below, which
only pass requests to the ``Desk``. A file is sent as the raw body of the request, its
name in a header: no form encoding, so no extra dependency. Messages meant for the
handler are in French; a request that cannot be served answers ``{"detail": message}``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from fraudcheck.demo.desk import MAX_REASON_CHARS, Desk, DeskError, DeskSettings, UnknownDocument
from fraudcheck.read.gateway import Gateway
from fraudcheck.read.settings import ReadSettings

STATIC = Path(__file__).parent / "static"
#: Heaviest file accepted. The pipeline itself makes lighter working copies of heavy pictures.
MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MAX_NAME_CHARS = 200
LOCAL_HOSTS = ("127.0.0.1", "localhost")
#: The page loads nothing from anywhere else, and no other site may frame it.
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' blob: data:; object-src 'none'; base-uri 'none'; "
                               "frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


class StartRequest(BaseModel):
    nature: str = Field(max_length=200)


class DecisionRequest(BaseModel):
    fake: bool
    reason: str = Field("", max_length=MAX_REASON_CHARS * 4)
    generated: bool = False


class FreshStatic(StaticFiles):
    """Static files the browser always checks again, so an edited page is never shown stale."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(
    settings: DeskSettings | None = None,
    *,
    allowed_hosts: Sequence[str] = LOCAL_HOSTS,
    live_gateway: Callable[[ReadSettings], Gateway] | None = None,
) -> FastAPI:
    """The application. ``settings`` default to a real session (``DeskSettings.from_repository``)."""
    desk = Desk(settings or DeskSettings.from_repository(), live_gateway=live_gateway)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        desk.close()  # deletes every file of the session

    app = FastAPI(title="Contrôle des justificatifs", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.desk = desk
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))
    app.mount("/static", FreshStatic(directory=STATIC), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    @app.exception_handler(DeskError)
    async def refused(_: Request, exc: DeskError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(UnknownDocument)
    async def unknown(_: Request, __: UnknownDocument) -> JSONResponse:
        return JSONResponse({"detail": "Ce document n'est plus dans la session."}, status_code=404)

    @app.get("/", include_in_schema=False)
    def page() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/setup")
    def setup() -> dict:
        return desk.setup()

    @app.get("/api/documents")
    def documents() -> list[dict]:
        return [document.as_dict() for document in desk.documents()]

    @app.post("/api/documents", status_code=201)
    async def add(request: Request) -> dict:
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/octet-stream":
            raise HTTPException(415, "Le fichier doit être envoyé tel quel (application/octet-stream).")
        data = await _body(request)
        name = Path(unquote(request.headers.get("x-file-name", ""))).name[:MAX_NAME_CHARS]
        document, duplicate = await run_in_threadpool(desk.add, name, data)
        return {**document.as_dict(), "duplicate": duplicate}

    @app.get("/api/documents/{key}")
    def one(key: str) -> dict:
        return desk.get(key).as_dict()

    @app.delete("/api/documents/{key}", status_code=204)
    def remove(key: str) -> Response:
        desk.remove(key)
        return Response(status_code=204)

    @app.post("/api/documents/{key}/analysis")
    def start(key: str, body: StartRequest) -> dict:
        return desk.start(key, body.nature).as_dict()

    @app.get("/api/documents/{key}/pages/{number}")
    def picture(key: str, number: int, turn: int = 0) -> Response:
        data = desk.page(key, number, turn)
        if data is None:
            raise HTTPException(404, "Aperçu indisponible pour cette page.")
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})

    @app.post("/api/documents/{key}/decision")
    def decide(key: str, body: DecisionRequest) -> dict:
        return desk.decide(key, fake=body.fake, reason=body.reason, generated=body.generated).as_dict()

    @app.delete("/api/documents/{key}/decision")
    def reopen(key: str) -> dict:
        return desk.reopen(key).as_dict()

    @app.get("/api/journal.csv")
    def journal() -> Response:
        return Response(desk.journal_csv(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="decisions.csv"', "Cache-Control": "no-store"})

    return app


async def _body(request: Request) -> bytes:
    """The request's body, refused as soon as it passes the size limit."""
    too_large = HTTPException(413, f"Fichier trop lourd : {MAX_UPLOAD_BYTES // (1024 * 1024)} Mo au plus.")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise too_large
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_UPLOAD_BYTES:
            raise too_large
        chunks.append(chunk)
    return b"".join(chunks)
