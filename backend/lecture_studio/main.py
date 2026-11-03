"""FastAPI application: REST API, SSE live updates and the static GUI."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from . import __version__
from .config import Settings, load_settings
from .db import Database
from .events import EventBus
from .jobs import JobManager
from .paths import sanitize_filename
from .services import SttService, TumService
from .stt import catalog
from .transcripts import export as exporter
from .transcripts import store as transcript_store
from .tumlive.client import AuthRequired, NotFound, TumLiveError
from .tumlive.models import PRIORITY_COURSES, Semester

log = logging.getLogger("lecture_studio")


class App:
    """Holds the long-lived services (one per process)."""

    def __init__(self, settings: Settings):
        settings.ensure_dirs()
        self.settings = settings
        self.db = Database(settings.db_path)
        self.bus = EventBus()
        self.tum = TumService(settings, self.db)
        self.stt = SttService(settings, self.db)
        self.jobs = JobManager(self.db, self.bus, settings, self.tum, self.stt)


# ---- request models ---------------------------------------------------------
class LoginBody(BaseModel):
    method: str = "sso"
    username: str
    password: str
    remember: bool = True


class ContinueBody(BaseModel):
    flow_id: str
    values: dict[str, str] = Field(default_factory=dict)
    choice: str | None = None
    remember: bool = True


class CookieBody(BaseModel):
    jwt: str
    remember: bool = True


class FavoriteBody(BaseModel):
    key: str
    on: bool


class DownloadItem(BaseModel):
    semester: str
    slug: str
    stream_id: int
    variant: str = "COMB"


class DownloadBody(BaseModel):
    items: list[DownloadItem]
    auto_transcribe: bool | None = None


class TranscribeItem(BaseModel):
    stream_id: int
    variant: str = "COMB"


class TranscribeBody(BaseModel):
    items: list[TranscribeItem]
    options: dict[str, Any] = Field(default_factory=dict)


class SegmentEdit(BaseModel):
    idx: int
    text: str


class EditBody(BaseModel):
    edits: list[SegmentEdit]


class SettingsBody(BaseModel):
    values: dict[str, Any]


SETTING_KEYS = {
    "stt.model",
    "stt.compute_type",
    "stt.language",
    "stt.vocabulary",
    "stt.vad",
    "stt.beam_size",
    "stt.remote_url",
    "auto_transcribe",
    "download.variant",
}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    state = App(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
        await state.jobs.start()
        yield
        await state.jobs.shutdown()
        await state.tum.aclose()
        state.db.close()

    app = FastAPI(title="TUM Lecture Studio", version=__version__, lifespan=lifespan)
    app.state.studio = state
    allowed_hosts = {
        "localhost",
        "127.0.0.1",
        "[::1]",
        "::1",
        "host.docker.internal",
        "testserver",
        *filter(None, os.environ.get("TLS_ALLOWED_HOSTS", "").split(",")),
    }

    @app.middleware("http")
    async def guard(request: Request, call_next):  # type: ignore[no-untyped-def]
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
        if host and host not in {h.strip("[]") for h in allowed_hosts}:
            # Blocks DNS-rebinding attacks against a localhost-bound app.
            return PlainTextResponse("Host not allowed", status_code=403)
        if (
            request.url.path.startswith("/api/")
            and request.method not in ("GET", "HEAD", "OPTIONS")
            and request.headers.get("x-studio") != "1"
        ):
            # Custom header forces a CORS preflight, which we never grant -> no cross-site POSTs.
            return PlainTextResponse("Missing X-Studio header", status_code=403)
        return await call_next(request)

    @app.exception_handler(AuthRequired)
    async def _auth(_: Request, exc: AuthRequired) -> JSONResponse:
        return JSONResponse(
            {"detail": "TUM-Live session expired or missing. Please sign in.", "code": "auth_required"},
            status_code=401,
        )

    @app.exception_handler(TumLiveError)
    async def _tum(_: Request, exc: TumLiveError) -> JSONResponse:
        return JSONResponse({"detail": f"TUM-Live error: {exc}", "code": "tumlive"}, status_code=502)

    # ---- system ----------------------------------------------------------
    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__}

    @app.get("/api/system")
    async def system() -> dict[str, Any]:
        hw = state.stt.hw
        return {
            "version": __version__,
            "hardware": hw.to_dict(),
            "recommendation": state.stt.recommendation(),
            "settings": {
                **state.stt.defaults(),
                "auto_transcribe": state.jobs._auto_transcribe(),
                "download_variant": state.db.get_setting("download.variant", "COMB"),
            },
            "paths": {
                "recordings": str(settings.recordings_dir),
                "transcripts": str(settings.transcripts_dir),
                "models": str(settings.models_dir),
                "data": str(settings.data_dir),
            },
            "priority_courses": [{"id": p["id"], "label": p["label"]} for p in PRIORITY_COURSES],
        }

    @app.put("/api/settings")
    async def put_settings(body: SettingsBody) -> dict[str, Any]:
        for k, v in body.values.items():
            if k not in SETTING_KEYS:
                raise HTTPException(400, f"unknown setting {k}")
            if k == "stt.model":
                try:
                    catalog.get_spec(str(v))
                except KeyError as exc:
                    raise HTTPException(400, f"unknown model {v}") from exc
            state.db.set_setting(k, v)
        return await system()

    # ---- session -----------------------------------------------------------
    @app.get("/api/session")
    async def session(force: bool = False) -> dict[str, Any]:
        return await state.tum.status(force=force)

    @app.post("/api/session/login")
    async def login(body: LoginBody) -> dict[str, Any]:
        return await state.tum.login(body.method, body.username, body.password, body.remember)

    @app.post("/api/session/continue")
    async def login_continue(body: ContinueBody) -> dict[str, Any]:
        return await state.tum.login_continue(body.flow_id, body.values, body.choice, body.remember)

    @app.post("/api/session/cookie")
    async def import_cookie(body: CookieBody) -> dict[str, Any]:
        return await state.tum.adopt_session(body.jwt, body.remember)

    @app.post("/api/session/logout")
    async def logout() -> dict[str, Any]:
        state.tum.logout()
        return await state.tum.status()

    # ---- catalog -------------------------------------------------------------
    @app.get("/api/semesters")
    async def semesters() -> dict[str, Any]:
        return await state.tum.semesters()

    def _local_index(stream_ids: list[int]) -> dict[int, dict[str, Any]]:
        idx: dict[int, dict[str, Any]] = {
            sid: {"downloaded": [], "transcripts": [], "jobs": []} for sid in stream_ids
        }
        for r in state.db.recordings_for_streams(stream_ids):
            if Path(r["path"]).exists():
                idx[r["stream_id"]]["downloaded"].append({"variant": r["variant"], "size": r["size"]})
        if stream_ids:
            q = ",".join("?" * len(stream_ids))
            for t in state.db.query(
                f"SELECT id, stream_id, variant, model FROM transcripts WHERE stream_id IN ({q})", stream_ids
            ):
                idx[t["stream_id"]]["transcripts"].append(t)
            for j in state.db.query(
                f"SELECT id, kind, status, stage, progress, variant, stream_id FROM jobs WHERE stream_id IN ({q}) "
                "AND status IN ('queued','running','failed') ORDER BY created_at DESC",
                stream_ids,
            ):
                idx[j["stream_id"]]["jobs"].append(j)
        return idx

    @app.get("/api/courses")
    async def courses(
        semester: str | None = None, refresh: bool = False, q: str | None = None
    ) -> dict[str, Any]:
        sem = (
            Semester.parse(semester) if semester else Semester.parse((await state.tum.semesters())["current"])
        )
        found = await state.tum.courses(sem, refresh=refresh, query=q)
        favs = state.db.favorites()
        sids = [r.id for c in found for r in c.recordings]
        local = _local_index(sids)
        items = []
        for c in found:
            d = c.to_public(favs)
            d["downloadable_count"] = sum(1 for r in c.recordings if r.is_vod and r.downloads)
            d["downloaded_count"] = sum(1 for r in c.recordings if local.get(r.id, {}).get("downloaded"))
            d["transcribed_count"] = sum(1 for r in c.recordings if local.get(r.id, {}).get("transcripts"))
            items.append(d)
        session_info = await state.tum.status()
        return {"semester": sem.key, "courses": items, "session": session_info}

    @app.get("/api/courses/{semester}/{slug}")
    async def course(semester: str, slug: str, refresh: bool = False) -> dict[str, Any]:
        sem = Semester.parse(semester)
        try:
            c = await state.tum.course(slug, sem, refresh=refresh)
        except NotFound as exc:
            raise HTTPException(404, "course not found or not visible to this account") from exc
        favs = state.db.favorites()
        recs = sorted(c.recordings, key=lambda r: r.start or "", reverse=True)
        local = _local_index([r.id for r in recs])
        return {
            "course": {**c.to_public(favs), "downloadable_count": sum(1 for r in recs if r.downloads)},
            "recordings": [
                {**r.to_public(), "local": local.get(r.id)} for r in recs if not r.is_planned or r.is_vod
            ],
            "upcoming": [r.to_public() for r in recs if r.is_planned and not r.is_vod],
        }

    @app.post("/api/favorites")
    async def favorite(body: FavoriteBody) -> dict[str, Any]:
        state.db.set_favorite(body.key, body.on)
        return {"favorites": sorted(state.db.favorites())}

    # ---- jobs ----------------------------------------------------------------
    @app.post("/api/downloads")
    async def downloads(body: DownloadBody) -> dict[str, Any]:
        results = []
        for it in body.items:
            sem = Semester.parse(it.semester)
            c = await state.tum.course(it.slug, sem)
            rec = next((r for r in c.recordings if r.id == it.stream_id), None)
            if rec is None:
                results.append({"stream_id": it.stream_id, "error": "recording not found"})
                continue
            if not rec.download_for(it.variant):
                results.append(
                    {
                        "stream_id": it.stream_id,
                        "error": f"downloads not permitted for {it.variant} of this lecture",
                    }
                )
                continue
            res = state.jobs.enqueue_download(
                course=c.to_public(),
                recording=rec.to_public(),
                variant=it.variant,
                auto_transcribe=body.auto_transcribe,
            )
            results.append({"stream_id": it.stream_id, **res})
        return {"results": results}

    @app.post("/api/transcribe")
    async def transcribe(body: TranscribeBody) -> dict[str, Any]:
        results = []
        for it in body.items:
            rec = state.db.get_recording(it.stream_id, it.variant)
            if not rec or not Path(rec["path"]).exists():
                results.append({"stream_id": it.stream_id, "error": "recording is not downloaded"})
                continue
            res = state.jobs.enqueue_transcribe(
                stream_id=it.stream_id,
                variant=it.variant,
                media_path=rec["path"],
                title=rec["title"],
                course_name=rec["course_name"],
                year=rec["year"],
                term=rec["term"],
                course_slug=rec["course_slug"],
                options=body.options,
            )
            results.append({"stream_id": it.stream_id, **res})
        return {"results": results}

    @app.get("/api/jobs")
    async def jobs(limit: int = 300) -> dict[str, Any]:
        return {"jobs": state.db.list_jobs(limit)}

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel(job_id: str) -> dict[str, Any]:
        job = await state.jobs.cancel(job_id)
        if not job:
            raise HTTPException(404)
        return job

    @app.post("/api/jobs/{job_id}/retry")
    async def retry(job_id: str) -> dict[str, Any]:
        job = state.jobs.retry(job_id)
        if not job:
            raise HTTPException(404)
        return job

    @app.delete("/api/jobs/{job_id}")
    async def delete_job(job_id: str) -> dict[str, Any]:
        if not state.jobs.remove(job_id):
            raise HTTPException(409, "job is active or unknown")
        return {"ok": True}

    @app.post("/api/jobs/clear")
    async def clear_jobs() -> dict[str, Any]:
        n = 0
        for j in state.db.list_jobs(10_000):
            if j["status"] in ("completed", "cancelled"):
                state.db.delete_job(j["id"])
                n += 1
        state.bus.publish("jobs_cleared", {"count": n})
        return {"removed": n}

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        q = state.bus.subscribe()

        async def gen():  # type: ignore[no-untyped-def]
            try:
                yield "event: hello\ndata: {}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=15)
                        yield msg
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                state.bus.unsubscribe(q)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ---- library ---------------------------------------------------------------
    @app.get("/api/library")
    async def library() -> dict[str, Any]:
        recs = state.db.list_recordings()
        local = _local_index([r["stream_id"] for r in recs])
        for r in recs:
            r["exists"] = Path(r["path"]).exists()
            r["transcripts"] = local.get(r["stream_id"], {}).get("transcripts", [])
        return {"recordings": recs}

    @app.post("/api/library/import")
    async def import_media(file: UploadFile = File(...)) -> dict[str, Any]:
        """Add a local media file (e.g. a test fixture or your own recording) for transcription."""
        name = sanitize_filename(file.filename or "upload", 100)
        dest_dir = settings.recordings_dir / "imported"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / name
        h = hashlib.sha1()
        with open(dest, "wb") as fh:
            while chunk := await file.read(1 << 20):
                h.update(chunk)
                fh.write(chunk)
        from .downloads.engine import DownloadError, probe

        try:
            info = await probe(dest)
        except DownloadError as exc:
            dest.unlink(missing_ok=True)
            raise HTTPException(400, f"not a media file: {exc}") from exc
        sid = -int(h.hexdigest()[:7], 16)  # negative ids never collide with TUM-Live stream ids
        state.db.upsert_recording(
            stream_id=sid,
            variant="FILE",
            course_name="Imported",
            title=Path(name).stem,
            path=str(dest),
            size=info.size,
            duration=info.duration,
            year=None,
            term=None,
        )
        state.bus.publish("library", {"stream_id": sid})
        return {"stream_id": sid, "variant": "FILE", "duration": info.duration}

    @app.delete("/api/library/{stream_id}/{variant}")
    async def delete_recording(stream_id: int, variant: str) -> dict[str, Any]:
        rec = state.db.get_recording(stream_id, variant)
        if not rec:
            raise HTTPException(404)
        Path(rec["path"]).unlink(missing_ok=True)
        state.db.delete_recording(stream_id, variant)
        state.bus.publish("library", {"stream_id": stream_id})
        return {"ok": True}

    @app.get("/api/media/{stream_id}/{variant}")
    async def media(stream_id: int, variant: str) -> FileResponse:
        rec = state.db.get_recording(stream_id, variant)
        if not rec or not Path(rec["path"]).exists():
            raise HTTPException(404)
        return FileResponse(rec["path"], media_type="video/mp4")

    # ---- transcripts -------------------------------------------------------------
    @app.get("/api/transcripts")
    async def transcripts() -> dict[str, Any]:
        return {
            "transcripts": state.db.query(
                "SELECT t.*, (SELECT COUNT(*) FROM segments s WHERE s.transcript_id=t.id) AS segment_count "
                "FROM transcripts t ORDER BY created_at DESC"
            )
        }

    @app.get("/api/transcripts/search")
    async def search(q: str) -> dict[str, Any]:
        return {"hits": transcript_store.search(state.db, q)}

    @app.get("/api/transcripts/{tid}")
    async def transcript(tid: str) -> dict[str, Any]:
        t = transcript_store.get(state.db, tid)
        if not t:
            raise HTTPException(404)
        rec = state.db.get_recording(t["stream_id"], t["variant"]) if t.get("stream_id") is not None else None
        t["media_available"] = bool(rec and Path(rec["path"]).exists())
        return t

    @app.patch("/api/transcripts/{tid}")
    async def edit_transcript(tid: str, body: EditBody) -> dict[str, Any]:
        t = transcript_store.update_segments(
            state.db, settings.transcripts_dir, tid, [e.model_dump() for e in body.edits]
        )
        if not t:
            raise HTTPException(404)
        return t

    @app.delete("/api/transcripts/{tid}")
    async def delete_transcript(tid: str) -> dict[str, Any]:
        transcript_store.delete(state.db, tid)
        return {"ok": True}

    @app.get("/api/transcripts/{tid}/export/{fmt}")
    async def export(tid: str, fmt: str) -> Response:
        t = transcript_store.get(state.db, tid)
        if not t or fmt not in exporter.FORMATS:
            raise HTTPException(404)
        body = exporter.export(fmt, t["segments"], transcript_store.meta(t))
        fname = sanitize_filename(f"{t.get('title') or 'transcript'}", 100)
        return Response(
            body,
            media_type=exporter.FORMATS[fmt],
            headers={"Content-Disposition": f'attachment; filename="{_ascii(fname)}.{fmt}"'},
        )

    # ---- models --------------------------------------------------------------------
    @app.get("/api/models")
    async def models() -> dict[str, Any]:
        return {"models": state.stt.list_models(), "recommendation": state.stt.recommendation()}

    @app.post("/api/models/{model_id}/download")
    async def download_model(model_id: str) -> dict[str, Any]:
        try:
            spec = catalog.get_spec(model_id)
        except KeyError as exc:
            raise HTTPException(404) from exc
        if spec.engine == "remote":
            raise HTTPException(400, "remote models are managed by the companion server")

        async def run() -> None:
            try:
                await asyncio.to_thread(state.stt.models.download, spec)
            except Exception as exc:  # noqa: BLE001
                log.warning("model download failed: %s", exc)
            state.bus.publish("models", {"id": model_id})

        asyncio.create_task(run())
        await asyncio.sleep(0.2)
        return {"started": True}

    @app.delete("/api/models/{model_id}")
    async def remove_model(model_id: str) -> dict[str, Any]:
        try:
            spec = catalog.get_spec(model_id)
        except KeyError as exc:
            raise HTTPException(404) from exc
        state.stt.models.remove(spec)
        return {"ok": True}

    @app.get("/api/benchmarks")
    async def benchmarks() -> dict[str, Any]:
        return {"benchmarks": state.db.query("SELECT * FROM benchmarks ORDER BY created_at DESC LIMIT 200")}

    # ---- static GUI ----------------------------------------------------------------------
    static_dir = settings.static_dir
    if static_dir and static_dir.exists():
        index = static_dir / "index.html"

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> Response:
            if path.startswith("api/"):
                raise HTTPException(404)
            f = (static_dir / path).resolve()
            if path and f.is_file() and static_dir.resolve() in f.parents:
                return FileResponse(f)
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
    else:

        @app.get("/", include_in_schema=False)
        async def no_gui() -> PlainTextResponse:
            return PlainTextResponse(
                "TUM Lecture Studio API is running. GUI not built: run `npm run build` in frontend/."
            )

    app.state.started = time.time()
    return app


def _ascii(s: str) -> str:
    return s.encode("ascii", "replace").decode().replace("?", "_").replace('"', "_")


def run() -> None:
    import copy
    import socket
    import sys
    import threading
    import webbrowser

    import uvicorn
    from uvicorn.config import LOGGING_CONFIG

    # Log to stdout: Windows PowerShell 5.1 renders every stderr line as a red
    # "NativeCommandError", which makes a healthy start look like a crash.
    logging.basicConfig(
        level=logging.INFO, stream=sys.stdout, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    log_config = copy.deepcopy(LOGGING_CONFIG)
    for handler in log_config["handlers"].values():
        handler["stream"] = "ext://sys.stdout"
    s = load_settings()
    if s.host not in ("127.0.0.1", "localhost", "::1") and os.environ.get("TLS_IN_DOCKER") != "1":
        log.warning("Binding to %s exposes the app beyond this machine. It has no login of its own!", s.host)
    url = f"http://{'127.0.0.1' if s.host in ('0.0.0.0', '::') else s.host}:{s.port}"
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1" if s.host in ("0.0.0.0", "::") else s.host, s.port)) == 0:
            print(f"Port {s.port} is already in use - TUM Lecture Studio is probably already running: {url}")
            print("Close the other window (Ctrl+C) or set TLS_PORT to use another port.")
            if os.environ.get("TLS_OPEN_BROWSER") == "1":
                webbrowser.open(url)
            raise SystemExit(1)
    if os.environ.get("TLS_OPEN_BROWSER") == "1":
        threading.Timer(2.5, lambda: webbrowser.open(url)).start()
    print(f"TUM Lecture Studio is running: {url}  (stop with Ctrl+C)", flush=True)
    uvicorn.run(create_app(s), host=s.host, port=s.port, log_level="info", log_config=log_config)


if __name__ == "__main__":
    run()
