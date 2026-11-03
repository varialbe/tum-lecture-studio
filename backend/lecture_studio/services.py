"""Application services shared by the API and the job manager."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from .config import Settings
from .db import Database
from .stt import catalog
from .stt.base import DEFAULT_VOCABULARY, SttUnavailable
from .stt.engines import FasterWhisperEngine, MlxWhisperEngine, RemoteEngine
from .stt.hardware import Hardware, detect
from .tumlive.auth import LoginManager, LoginStep
from .tumlive.client import AuthRequired, SessionStore, TumLiveClient, TumLiveError
from .tumlive.models import Course, Recording, Semester


class TumService:
    COURSE_TTL = 120.0
    LIST_TTL = 300.0

    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self.store = SessionStore(settings.session_path)
        saved = self.store.load()
        self.client = TumLiveClient(settings.tum_base_url, jwt=saved["jwt"] if saved else None)
        self.display_name: str | None = saved.get("display_name") if saved else None
        self.state = "unknown" if saved else "anonymous"  # anonymous | authenticated | expired | unknown
        self.logins = LoginManager(settings.tum_base_url)
        self._course_cache: dict[str, tuple[float, Course]] = {}
        self._list_cache: dict[str, tuple[float, list[Course]]] = {}
        self._checked = 0.0

    async def aclose(self) -> None:
        await self.client.aclose()

    # ---- session -----------------------------------------------------------
    async def status(self, force: bool = False) -> dict[str, Any]:
        if self.client.jwt and (force or self.state == "unknown" or time.time() - self._checked > 300):
            try:
                user = await self.client.me()
            except TumLiveError:
                user = None
                if self.state == "unknown":
                    return {
                        "state": "unknown",
                        "display_name": self.display_name,
                        "message": "TUM-Live is unreachable; session not verified",
                    }
            self._checked = time.time()
            if user:
                self.state = "authenticated"
                self.display_name = _display(user)
            else:
                self.state = "expired"
        return {"state": self.state, "display_name": self.display_name if self.state != "anonymous" else None}

    async def adopt_session(self, jwt: str, remember: bool = True) -> dict[str, Any]:
        jwt = jwt.strip()
        if jwt.lower().startswith("jwt="):
            jwt = jwt[4:]
        probe = TumLiveClient(self.settings.tum_base_url, jwt=jwt)
        try:
            user = await probe.me()
        finally:
            await probe.aclose()
        if not user:
            return {"state": "error", "message": "TUM-Live did not accept this session (expired or invalid)."}
        self.client.set_jwt(jwt)
        self.state, self.display_name, self._checked = "authenticated", _display(user), time.time()
        if remember:
            self.store.save(jwt, self.display_name)
        else:
            self.store.clear()
        self._invalidate()
        return await self.status()

    async def login(self, method: str, username: str, password: str, remember: bool) -> dict[str, Any]:
        step = await self.logins.login(method, username, password)
        return await self._login_result(step, remember)

    async def login_continue(
        self, flow_id: str, values: dict[str, Any], choice: str | None, remember: bool
    ) -> dict[str, Any]:
        step = await self.logins.resume(flow_id, values, choice)
        return await self._login_result(step, remember)

    async def _login_result(self, step: LoginStep, remember: bool) -> dict[str, Any]:
        if step.status == "ok" and step.jwt:
            return await self.adopt_session(step.jwt, remember)
        return {
            "state": step.status,
            "flow_id": step.flow_id,
            "message": step.message,
            "inputs": step.inputs,
            "choices": step.choices,
        }

    def logout(self) -> None:
        self.client.set_jwt(None)
        self.store.clear()
        self.state, self.display_name = "anonymous", None
        self._invalidate()

    def _invalidate(self) -> None:
        self._course_cache.clear()
        self._list_cache.clear()

    def _expired(self) -> None:
        if self.client.jwt:
            self.state = "expired"

    # ---- data ----------------------------------------------------------------
    async def semesters(self) -> dict[str, Any]:
        cur, sems = await self.client.semesters()
        return {"current": cur.key, "semesters": [{"key": s.key, "label": s.label} for s in sems]}

    async def courses(self, sem: Semester, refresh: bool = False, query: str | None = None) -> list[Course]:
        ck = f"{sem.key}|{query or ''}|{self.state}"
        hit = self._list_cache.get(ck)
        if hit and not refresh and time.time() - hit[0] < self.LIST_TTL:
            return hit[1]
        try:
            found = await self.client.discover(sem, [query] if query else None)
        except AuthRequired:
            self._expired()
            raise
        # Fetch details (recording lists) concurrently so cards can show counts.
        limiter = asyncio.Semaphore(6)

        async def detail(c: Course) -> Course:
            async with limiter:
                try:
                    full = await self.course(c.slug, sem, refresh=refresh)
                    full.source |= c.source
                    full.pinned = full.pinned or c.pinned
                    return full
                except TumLiveError:
                    return c

        result = list(await asyncio.gather(*(detail(c) for c in found)))
        self._list_cache[ck] = (time.time(), result)
        return result

    async def course(self, slug: str, sem: Semester, refresh: bool = False) -> Course:
        key = f"{sem.key}/{slug}"
        hit = self._course_cache.get(key)
        if hit and not refresh and time.time() - hit[0] < self.COURSE_TTL:
            return hit[1]
        try:
            c = await self.client.course(slug, sem)
        except AuthRequired:
            self._expired()
            raise
        self._course_cache[key] = (time.time(), c)
        return c

    async def fresh_recording(self, slug: str, sem: Semester, stream_id: int) -> Recording | None:
        """Re-fetch to get freshly signed URLs (they expire after some hours)."""
        c = await self.course(slug, sem, refresh=True)
        return next((r for r in c.recordings if r.id == stream_id), None)


def _display(user: dict[str, Any]) -> str:
    return (user.get("name") or user.get("lrzId") or "TUM user").strip()


class SttService:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self.models = catalog.ModelStore(settings.models_dir)

    @property
    def hw(self) -> Hardware:
        return detect()

    def recommendation(self) -> dict[str, str]:
        mid, ct, why = catalog.recommend(self.hw, bool(self.settings.stt_remote_url))
        return {"model": mid, "compute_type": ct, "reason": why}

    def defaults(self) -> dict[str, Any]:
        rec = self.recommendation()
        return {
            "model": self.db.get_setting("stt.model", self.settings.stt_default_model or rec["model"]),
            "compute_type": self.db.get_setting("stt.compute_type", rec["compute_type"]),
            "language": self.db.get_setting("stt.language", "de"),
            "vocabulary": self.db.get_setting("stt.vocabulary", DEFAULT_VOCABULARY),
            "vad": self.db.get_setting("stt.vad", True),
            "beam_size": self.db.get_setting("stt.beam_size", 5),
            "remote_url": self.db.get_setting("stt.remote_url", self.settings.stt_remote_url),
        }

    def job_params(self, options: dict[str, Any]) -> dict[str, Any]:
        d = self.defaults()
        d.update({k: v for k, v in options.items() if v is not None and k in d})
        for k in ("clip_start", "clip_seconds"):  # optional excerpt, e.g. a 3-minute benchmark sample
            if options.get(k) is not None:
                d[k] = float(options[k])
        return d

    def list_models(self) -> list[dict[str, Any]]:
        hw = self.hw
        defaults = self.defaults()
        bench = self.db.query(
            "SELECT engine, model, compute_type, device, COUNT(*) n, AVG(rtf) rtf, MAX(peak_mem_mb) peak_mem_mb, "
            "SUM(audio_seconds) audio_seconds FROM benchmarks GROUP BY engine, model, compute_type, device"
        )
        out = []
        specs = [*catalog.CATALOG]
        if defaults.get("remote_url"):
            specs.append(catalog.REMOTE_MODEL)
        for spec in specs:
            d = spec.to_dict()
            d.update(
                compatible=catalog.compatible(spec, hw),
                installed=self.models.installed(spec),
                size_on_disk=self.models.size_on_disk(spec) if spec.engine != "remote" else 0,
                download=self.models.download_state(spec.id),
                compute_types=catalog.compute_types(spec, hw),
                default=defaults["model"] == spec.id,
                benchmarks=[b for b in bench if b["model"] == spec.id],
            )
            out.append(d)
        return out

    async def engine_for(
        self, params: dict[str, Any], report: Callable[[str, float | None], Any]
    ) -> tuple[Any, dict[str, Any]]:
        model_id = params.get("model") or self.defaults()["model"]
        try:
            spec = catalog.get_spec(model_id)
        except KeyError as exc:
            raise SttUnavailable(f"Unknown model {model_id}") from exc
        hw = self.hw
        if spec.engine == "remote":
            url = params.get("remote_url") or self.defaults()["remote_url"]
            if not url:
                raise SttUnavailable("No STT companion URL configured")
            return RemoteEngine(url), {
                "engine": "remote",
                "model": f"remote:{url}",
                "device": "remote",
                "device_label": f"remote {url}",
                "compute_type": "default",
            }
        if not catalog.compatible(spec, hw):
            raise SttUnavailable(f"{spec.label} is not supported on this machine")
        if not self.models.installed(spec):
            report(f"Downloading model {spec.label} (~{spec.download_gb} GB)", None)
            task = asyncio.create_task(asyncio.to_thread(self.models.download, spec))
            # The download may already be running (started from the Models page): wait for that one too.
            while not task.done() or (self.models.download_state(spec.id) or {}).get("status") == "running":
                st = self.models.download_state(spec.id) or {}
                if st.get("bytes_total"):
                    report(f"Downloading model {spec.label}", st["bytes_done"] / st["bytes_total"])
                await asyncio.sleep(1)
            await task
            if not self.models.installed(spec):
                err = (self.models.download_state(spec.id) or {}).get("error", "unknown error")
                raise SttUnavailable(f"Model download failed: {err}")
        path = self.models.local_path(spec)
        assert path
        if spec.engine == "mlx-whisper":
            return MlxWhisperEngine(path, spec.id), {
                "engine": spec.engine,
                "model": spec.id,
                "device": "metal",
                "device_label": f"{hw.cpu} (Metal)",
                "compute_type": "float16",
            }
        device = "cuda" if hw.cuda_usable else "cpu"
        ct = params.get("compute_type") or "default"
        if device == "cpu" and ct in ("float16", "int8_float16"):
            ct = "int8"
        label = hw.cuda_devices[0]["name"] if device == "cuda" and hw.cuda_devices else f"{hw.cpu} (CPU)"
        return FasterWhisperEngine(path, spec.id, device=device, compute_type=ct), {
            "engine": spec.engine,
            "model": spec.id,
            "device": device,
            "device_label": label,
            "compute_type": ct,
        }
