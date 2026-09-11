import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
import hmac
import math
import os
from pathlib import Path
import sqlite3

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .build import VERSION, build_id
from .database import Database
from .models import Event, Provider, SessionCreate, SessionPatch, State

STATIC = Path(__file__).parent / "static"
MAX_BODY = 65536


@dataclass
class Settings:
    database: str = "data/slopwatchdeluxe.db"
    archive_after_hours: float = 24
    api_token: str = ""

    def __post_init__(self):
        if not math.isfinite(self.archive_after_hours) or self.archive_after_hours <= 0:
            raise ValueError("SLOPWATCHDELUXE_ARCHIVE_AFTER_HOURS must be a positive finite number")

    @classmethod
    def from_env(cls):
        return cls(os.getenv("SLOPWATCHDELUXE_DATABASE", "data/slopwatchdeluxe.db"),
                   float(os.getenv("SLOPWATCHDELUXE_ARCHIVE_AFTER_HOURS", "24")),
                   os.getenv("SLOPWATCHDELUXE_API_TOKEN", ""))


class RequestGuard:
    """Bound actual streamed bytes, including chunked requests, before JSON parsing."""
    def __init__(self, app, token):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        path = scope["path"]
        if path.startswith("/api/") and path != "/api/v1/health" and self.token:
            expected = ("Bearer " + self.token).encode()
            if not hmac.compare_digest(headers.get(b"authorization", b""), expected):
                return await JSONResponse({"detail": "API token required or invalid"}, 401,
                                          headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
        if scope["method"] in ("POST", "PATCH", "PUT"):
            chunks, size = [], 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > MAX_BODY:
                    return await JSONResponse({"detail": "Request exceeds 64 KiB"}, 413)(scope, receive, send)
                chunks.append(chunk)
                if not message.get("more_body"):
                    break
            if size and headers.get(b"content-type", b"").split(b";")[0] != b"application/json":
                return await JSONResponse({"detail": "Use application/json"}, 415)(scope, receive, send)
            delivered = False

            async def bounded_receive():
                nonlocal delivered
                if delivered:
                    return await receive()
                delivered = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            recv = bounded_receive
        else:
            recv = receive

        async def secure_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message["headers"]) + [
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self'; "
                     b"img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
                ]
                if path.startswith("/api/"):
                    message["headers"].append((b"cache-control", b"no-store"))
            await send(message)
        await self.app(scope, recv, secure_send)


def create_app(settings=None):
    settings = settings or Settings.from_env()
    build = build_id()

    @asynccontextmanager
    async def lifespan(app):
        db = app.state.db = Database(settings.database)
        db.archive_inactive(settings.archive_after_hours)

        async def archive_loop():
            while True:
                await asyncio.sleep(60)
                await asyncio.to_thread(db.archive_inactive, settings.archive_after_hours)
        task = asyncio.create_task(archive_loop())

        async def permission_loop():
            while True:
                await asyncio.to_thread(db.settle_permissions)
                await asyncio.sleep(0.5)
        permission_task = asyncio.create_task(permission_loop())
        try:
            yield
        finally:
            task.cancel()
            permission_task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            with suppress(asyncio.CancelledError):
                await permission_task
            db.close()

    app = FastAPI(title="SlopWatchDeluxe", version=VERSION, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.add_middleware(RequestGuard, token=settings.api_token)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": "Session not found"}, 404)

    @app.exception_handler(sqlite3.IntegrityError)
    async def conflict(request, exc):
        return JSONResponse({"detail": "Session identity already exists"}, 409)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/slopwatchdeluxe.pyz", include_in_schema=False)
    def client_download():
        artifact = Path(__file__).resolve().parent.parent / "dist" / "slopwatchdeluxe.pyz"
        if not artifact.is_file():
            raise HTTPException(404, "Build the client with python scripts/build-zipapp.py")
        return FileResponse(artifact, filename="slopwatchdeluxe.pyz", media_type="application/octet-stream")

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok" if app.state.db.healthy() else "error", "version": VERSION,
                "build": build, "auth_required": bool(settings.api_token)}

    @app.post("/api/v1/sessions", status_code=201)
    def create_session(data: SessionCreate):
        return app.state.db.create(data)

    @app.get("/api/v1/sessions")
    def list_sessions(archived: bool = False, state: State | None = None,
                      provider: Provider | None = None, search: str | None = Query(None, max_length=255),
                      limit: int = Query(1000, ge=1, le=1000), offset: int = Query(0, ge=0)):
        return app.state.db.list(archived, state, provider, search, limit, offset)

    @app.get("/api/v1/sessions/{session_id}")
    def get_session(session_id: str):
        return app.state.db.get(session_id)

    @app.patch("/api/v1/sessions/{session_id}")
    def patch_session(session_id: str, data: SessionPatch):
        return app.state.db.patch(session_id, data.model_dump(exclude_unset=True))

    @app.delete("/api/v1/sessions/{session_id}", status_code=204)
    def delete_session(session_id: str):
        app.state.db.delete(session_id)
        return Response(status_code=204)

    @app.post("/api/v1/events")
    def event(data: Event):
        session = app.state.db.event(data)
        return session if session is not None else Response(status_code=204)

    @app.post("/api/v1/sessions/{session_id}/archive")
    def archive_session(session_id: str):
        return app.state.db.archive(session_id)

    @app.post("/api/v1/sessions/{session_id}/restore")
    def restore_session(session_id: str):
        return app.state.db.archive(session_id, restore=True)

    @app.get("/api/v1/stream")
    async def stream(request: Request):
        async def updates():
            revision, ticks = -1, 0
            while not await request.is_disconnected():
                current = app.state.db.revision
                if current != revision:
                    yield f"event: change\ndata: {current}\n\n"
                    revision = current
                elif ticks % 15 == 0:
                    yield ": heartbeat\n\n"
                ticks += 1
                await asyncio.sleep(1)
        return StreamingResponse(updates(), media_type="text/event-stream",
                                 headers={"X-Accel-Buffering": "no"})

    return app


app = create_app()
