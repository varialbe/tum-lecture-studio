"""Persistent background jobs: downloads and transcriptions.

Job rows live in SQLite, so the queue survives restarts: on startup jobs that
were `running` are re-queued (downloads resume from their .part file,
transcriptions from their finished chunks).
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import Database
from .downloads.engine import (
    DownloadError,
    Progress,
    _ytdlp_available,
    ffmpeg_hls_download,
    hls_duration,
    http_range_download,
    probe,
    verify_media,
    ytdlp_hls_download,
)
from .events import EventBus
from .paths import recording_path, sanitize_filename, semester_folder
from .stt.audio import cut_chunk, detect_silences, extract_audio, wav_duration
from .stt.base import Segment, TranscribeOptions, Word, build_prompt
from .stt.chunking import merge_chunks, plan_chunks
from .stt.memory import MemorySampler
from .transcripts import store as transcript_store
from .tumlive.client import AuthRequired, TumLiveError
from .tumlive.models import Semester

log = logging.getLogger("lecture_studio.jobs")

STAGES = [
    "queued",
    "authenticating",
    "preparing",
    "downloading",
    "verifying",
    "download_complete",
    "extracting_audio",
    "loading_model",
    "transcribing",
    "exporting",
    "complete",
    "failed",
    "cancelled",
]
MAX_ATTEMPTS = 4
RETRY_BASE_SECONDS = 5.0


class JobFailed(Exception):
    def __init__(self, message: str, retry: bool = False):
        super().__init__(message)
        self.retry = retry


@dataclass
class _Throttle:
    interval: float = 0.5
    last: float = 0.0

    def ready(self) -> bool:
        now = time.monotonic()
        if now - self.last >= self.interval:
            self.last = now
            return True
        return False


class JobManager:
    def __init__(self, db: Database, bus: EventBus, settings: Any, tum: Any, stt: Any):
        self.db = db
        self.bus = bus
        self.settings = settings
        self.tum = tum
        self.stt = stt
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self.download_transport: Any = None  # injectable for tests
        self._sem = {
            "download": asyncio.Semaphore(settings.download_concurrency),
            "transcribe": asyncio.Semaphore(settings.transcribe_concurrency),
        }

    # ---- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        for job in self.db.list_jobs(limit=10_000):
            if job["status"] == "running":
                self.db.update_job(
                    job["id"], status="queued", stage="queued", message="Re-queued after application restart"
                )
            if job["status"] in ("queued", "running"):
                self._schedule(job["id"])

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # Leave them 'running' -> re-queued on next start (shutdown is not a user cancel).

    def _schedule(self, job_id: str) -> None:
        if job_id in self._tasks and not self._tasks[job_id].done():
            return
        self._tasks[job_id] = asyncio.create_task(self._run(job_id), name=f"job-{job_id}")

    def _emit(self, job: dict[str, Any] | None) -> None:
        if job:
            self.bus.publish("job", job)

    def _update(self, job_id: str, **fields: Any) -> dict[str, Any] | None:
        job = self.db.update_job(job_id, **fields)
        self._emit(job)
        return job

    # ---- public API ------------------------------------------------------
    def enqueue_download(
        self,
        *,
        course: dict[str, Any],
        recording: dict[str, Any],
        variant: str,
        auto_transcribe: bool | None = None,
    ) -> dict[str, Any]:
        sid = int(recording["id"])
        existing = self.db.get_recording(sid, variant)
        if existing and Path(existing["path"]).exists():
            return {"duplicate": "downloaded", "recording": existing}
        active = self.db.active_job_for("download", sid, variant)
        if active:
            return {"duplicate": "queued", "job": active}
        job = self.db.create_job(
            "download",
            stream_id=sid,
            course_slug=course["slug"],
            course_name=course["name"],
            year=course["year"],
            term=course["term"],
            title=recording["name"],
            variant=variant,
            params={
                "course_id": course.get("id"),
                "start": recording.get("start"),
                "duration": recording.get("duration"),
                "auto_transcribe": self._auto_transcribe() if auto_transcribe is None else auto_transcribe,
            },
        )
        self._emit(job)
        self._schedule(job["id"])
        return {"job": job}

    def enqueue_transcribe(
        self,
        *,
        stream_id: int,
        variant: str,
        media_path: str,
        title: str,
        course_name: str | None,
        year: int | None,
        term: str | None,
        course_slug: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        active = self.db.active_job_for("transcribe", stream_id, variant)
        if active:
            return {"duplicate": "queued", "job": active}
        params = self.stt.job_params(options or {})
        params["media_path"] = media_path
        job = self.db.create_job(
            "transcribe",
            stream_id=stream_id,
            variant=variant,
            title=title,
            course_name=course_name,
            course_slug=course_slug,
            year=year,
            term=term,
            params=params,
        )
        self._emit(job)
        self._schedule(job["id"])
        return {"job": job}

    async def cancel(self, job_id: str) -> dict[str, Any] | None:
        job = self.db.get_job(job_id)
        if not job or job["status"] not in ("queued", "running"):
            return job
        self.db.update_job(
            job_id,
            status="cancelled",
            stage="cancelled",
            finished_at=time.time(),
            message="Cancelled by user",
            speed=None,
        )
        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
        job = self.db.get_job(job_id)
        self._emit(job)
        return job

    def retry(self, job_id: str) -> dict[str, Any] | None:
        job = self.db.get_job(job_id)
        if not job or job["status"] in ("queued", "running"):
            return job
        job = self._update(
            job_id,
            status="queued",
            stage="queued",
            error=None,
            message="Retry requested",
            attempts=0,
            finished_at=None,
        )
        self._schedule(job_id)
        return job

    def remove(self, job_id: str) -> bool:
        job = self.db.get_job(job_id)
        if not job or job["status"] in ("queued", "running"):
            return False
        self.db.delete_job(job_id)
        self.bus.publish("job_removed", {"id": job_id})
        return True

    def stream_capture_allowed(self) -> bool:
        return bool(self.db.get_setting("download.stream_capture", self.settings.stream_capture))

    def _auto_transcribe(self) -> bool:
        return bool(self.db.get_setting("auto_transcribe", self.settings.auto_transcribe))

    # ---- runner ------------------------------------------------------------
    async def _run(self, job_id: str) -> None:
        job = self.db.get_job(job_id)
        if not job or job["status"] not in ("queued", "running"):
            return
        sem = self._sem[job["kind"]]
        try:
            async with sem:
                job = self.db.get_job(job_id)
                if not job or job["status"] != "queued":
                    return
                while True:
                    job = self._update(
                        job_id,
                        status="running",
                        started_at=job.get("started_at") or time.time(),
                        attempts=(job["attempts"] or 0) + 1,
                        error=None,
                    )
                    assert job is not None
                    try:
                        if job["kind"] == "download":
                            await self._download(job)
                        else:
                            await self._transcribe(job)
                        return
                    except JobFailed as exc:
                        if exc.retry and job["attempts"] < MAX_ATTEMPTS:
                            delay = min(120, RETRY_BASE_SECONDS * 2 ** (job["attempts"] - 1))
                            self._update(
                                job_id,
                                stage="queued",
                                message=f"{exc} - retrying in {delay}s "
                                f"(attempt {job['attempts']}/{MAX_ATTEMPTS})",
                            )
                            await asyncio.sleep(delay)
                            job = self.db.get_job(job_id)
                            if not job or job["status"] != "running":
                                return
                            continue
                        self._update(
                            job_id,
                            status="failed",
                            stage="failed",
                            error=str(exc),
                            finished_at=time.time(),
                            speed=None,
                        )
                        return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface any bug as a failed job, never crash the app
            log.exception("job %s crashed", job_id)
            self._update(
                job_id,
                status="failed",
                stage="failed",
                error=f"{type(exc).__name__}: {exc}",
                finished_at=time.time(),
                speed=None,
            )
        finally:
            self._tasks.pop(job_id, None)

    # ---- download pipeline -------------------------------------------------
    async def _download(self, job: dict[str, Any]) -> None:
        jid = job["id"]
        p = job["params"]
        self._update(
            jid, stage="authenticating", message="Requesting a fresh signed URL from TUM-Live", progress=None
        )
        sem = Semester(int(job["year"]), job["term"])
        try:
            rec = await self.tum.fresh_recording(job["course_slug"], sem, int(job["stream_id"]))
        except AuthRequired as exc:
            raise JobFailed("TUM-Live session expired or missing - please sign in again") from exc
        except TumLiveError as exc:
            raise JobFailed(f"TUM-Live API error: {exc}", retry=True) from exc
        if rec is None:
            raise JobFailed("Recording no longer listed by TUM-Live (or not visible to this account)")
        dl = rec.download_for(job["variant"])
        hls_url = rec.playlists.get(job["variant"])
        capture = dl is None
        if capture and not self.stream_capture_allowed():
            raise JobFailed(
                f"The lecturer has not enabled downloads for this lecture ({job['variant']}). "
                "Turn on 'Record streams' in Models & settings to record the stream instead."
            )
        if capture and not hls_url:
            raise JobFailed(f"No {job['variant']} stream available for this lecture")
        dest = recording_path(
            self.settings.recordings_dir,
            year=sem.year,
            term=sem.term,
            course_name=job["course_name"],
            course_slug=job["course_slug"],
            stream_id=rec.id,
            date=rec.start,
            title=rec.name,
            variant=job["variant"],
        )
        part = dest.with_name(dest.name + ".part")
        throttle = _Throttle()

        def on_progress(pr: Progress) -> None:
            if pr.stage == "preparing":
                self._update(
                    jid,
                    stage="preparing",
                    progress=None,
                    bytes_done=pr.bytes_done,
                    message=(
                        "Starting stream recording"
                        if capture
                        else "Waiting for TUM-Live to prepare the file"
                        + (f" (resuming at {pr.bytes_done / 1e6:.0f} MB)" if pr.bytes_done else "")
                    ),
                )
            elif throttle.ready() or pr.fraction == 1.0:
                self.db.update_job(
                    jid,
                    stage="downloading",
                    progress=pr.fraction,
                    bytes_done=pr.bytes_done,
                    bytes_total=pr.bytes_total,
                    speed=pr.speed,
                    message="Recording stream" if capture else None,
                )
                self._emit(self.db.get_job(jid))

        expected = rec.duration
        try:
            if capture:
                assert hls_url
                expected = await hls_duration(hls_url) or rec.duration
                part = dest.with_name(dest.stem + ".capture.mp4")
                if _ytdlp_available():
                    try:
                        await ytdlp_hls_download(hls_url, part, on_progress)
                    except DownloadError as exc:
                        if exc.expired or exc.transient:
                            raise
                        self._update(jid, message=f"yt-dlp failed ({exc}); using ffmpeg")
                        await ffmpeg_hls_download(hls_url, part, on_progress, duration=expected)
                else:
                    await ffmpeg_hls_download(hls_url, part, on_progress, duration=expected)
            else:
                assert dl is not None
                try:
                    await http_range_download(
                        dl.url,
                        part,
                        on_progress,
                        first_byte_timeout=self.settings.download_first_byte_timeout,
                        transport=self.download_transport,
                    )
                except DownloadError as exc:
                    if exc.expired:
                        raise
                    if not exc.transient:
                        # e.g. server handed us a playlist: remux the HLS stream without transcoding.
                        self._update(jid, message=f"Direct file unavailable ({exc}); using HLS remux")
                        await ffmpeg_hls_download(dl.url, part, on_progress, duration=rec.duration)
                    else:
                        raise
        except DownloadError as exc:
            if exc.expired:
                raise JobFailed(f"Download link rejected ({exc}); will fetch a new one", retry=True) from exc
            raise JobFailed(f"Download failed: {exc}", retry=exc.transient) from exc

        self._update(jid, stage="verifying", progress=None, speed=None, message="Checking file integrity")
        try:
            info, warnings = await verify_media(part, expected)
            if capture and expected and info.duration < expected * 0.97 - 5:
                raise DownloadError(f"recording is incomplete ({info.duration:.0f}s of {expected:.0f}s)")
        except DownloadError as exc:
            part.unlink(missing_ok=True)
            raise JobFailed(f"Downloaded file is invalid: {exc}", retry=True) from exc
        part.replace(dest)
        self.db.upsert_recording(
            stream_id=rec.id,
            variant=job["variant"],
            course_id=p.get("course_id"),
            course_slug=job["course_slug"],
            course_name=job["course_name"],
            year=sem.year,
            term=sem.term,
            title=rec.name,
            start=rec.start,
            duration=info.duration,
            path=str(dest),
            size=info.size,
        )
        self._update(
            jid,
            status="completed",
            stage="download_complete",
            progress=1.0,
            bytes_done=info.size,
            bytes_total=info.size,
            output_path=str(dest),
            finished_at=time.time(),
            speed=None,
            message="; ".join(warnings)
            or f"{info.size / 1e6:.0f} MB, {info.duration / 60:.0f} min"
            + (" (recorded from stream)" if capture else ""),
        )
        self.bus.publish("library", {"stream_id": rec.id})
        if p.get("auto_transcribe"):
            self.enqueue_transcribe(
                stream_id=rec.id,
                variant=job["variant"],
                media_path=str(dest),
                title=rec.name,
                course_name=job["course_name"],
                year=sem.year,
                term=sem.term,
                course_slug=job["course_slug"],
            )

    # ---- transcription pipeline -------------------------------------------
    async def _transcribe(self, job: dict[str, Any]) -> None:
        jid = job["id"]
        p = job["params"]
        media = Path(p["media_path"])
        if not media.exists():
            raise JobFailed(f"Media file not found: {media}")
        work = self.settings.tmp_dir / f"tx-{jid}"
        work.mkdir(parents=True, exist_ok=True)
        t0 = time.time()

        self._update(jid, stage="verifying", progress=None, message="Checking media")
        try:
            info = await probe(media)
        except DownloadError as exc:
            raise JobFailed(f"Media is unreadable: {exc}") from exc
        if not info.has_audio:
            raise JobFailed("Media has no audio track")

        wav = work / "audio.wav"
        if not wav.exists():
            self._update(jid, stage="extracting_audio", progress=0.0, message="Extracting 16 kHz mono audio")
            th = _Throttle()

            def on_extract(frac: float | None) -> None:
                if th.ready():
                    self._update(jid, progress=frac)

            await extract_audio(
                media,
                wav,
                info.duration,
                on_extract,
                start=p.get("clip_start"),
                seconds=p.get("clip_seconds"),
            )
        duration = await wav_duration(wav)

        self._update(jid, stage="loading_model", progress=None, message="Preparing speech-to-text model")
        engine, desc = await self.stt.engine_for(
            p, lambda msg, frac: self._update(jid, message=msg, progress=frac)
        )
        silences = await detect_silences(wav)
        chunk_len = 300.0 if engine.engine == "remote" else 600.0
        chunks = plan_chunks(duration, silences, target=chunk_len)
        opts = TranscribeOptions(
            language=p.get("language") or None,
            initial_prompt=build_prompt(p.get("vocabulary")),
            vad=p.get("vad", True),
            beam_size=int(p.get("beam_size", 5)),
        )

        done_seconds = 0.0
        results: list[tuple[Any, list[Segment]]] = []
        detected_lang = None
        stt_wall = 0.0
        loop = asyncio.get_running_loop()
        sampler = MemorySampler(cuda=desc.get("device") == "cuda")
        sampler.start()
        try:
            for ch in chunks:
                cache = work / f"chunk-{ch.index:03d}.json"
                if cache.exists():
                    data = json.loads(cache.read_text(encoding="utf-8"))
                    segs = [_seg_from_dict(s) for s in data["segments"]]
                    detected_lang = detected_lang or data.get("language")
                    stt_wall += data.get("wall", 0.0)
                else:
                    cwav = work / f"chunk-{ch.index:03d}.wav"
                    await cut_chunk(wav, ch, cwav)
                    base_done = done_seconds
                    th = _Throttle()
                    started = time.monotonic()

                    def on_prog(
                        frac: float,
                        _base: float = base_done,
                        _ch: Any = ch,
                        _th: _Throttle = th,
                        _st: float = started,
                    ) -> None:
                        if not _th.ready():
                            return
                        processed = _base + frac * _ch.audio_duration
                        el = time.monotonic() - _st
                        speed = (frac * _ch.audio_duration / el) if el > 1 and frac > 0 else None
                        loop.call_soon_threadsafe(
                            lambda: self._update(
                                jid,
                                stage="transcribing",
                                progress=min(1.0, processed / duration),
                                speed=speed,
                                message=f"Chunk {_ch.index + 1}/{len(chunks)}",
                            )
                        )

                    self._update(
                        jid,
                        stage="transcribing",
                        progress=done_seconds / duration if duration else None,
                        message=f"Chunk {ch.index + 1}/{len(chunks)}",
                    )
                    # Note: a cancel stops the job right away; the worker thread finishes the
                    # current chunk in the background (inference can't be interrupted safely).
                    try:
                        res = await asyncio.to_thread(engine.transcribe, cwav, opts, on_prog)
                    except Exception as exc:  # noqa: BLE001
                        raise JobFailed(f"Transcription failed in chunk {ch.index + 1}: {exc}") from exc
                    wall = time.monotonic() - started
                    stt_wall += wall
                    segs = res.segments
                    detected_lang = detected_lang or res.language
                    cache.write_text(
                        json.dumps(
                            {
                                "language": res.language,
                                "wall": wall,
                                "segments": [_seg_to_dict(s) for s in segs],
                            }
                        ),
                        encoding="utf-8",
                    )
                    cwav.unlink(missing_ok=True)
                results.append((ch, segs))
                done_seconds += ch.audio_duration
        finally:
            peak = sampler.stop()

        self._update(
            jid, stage="exporting", progress=None, speed=None, message="Merging and saving transcript"
        )
        merged = merge_chunks(results)
        total_wall = time.time() - t0
        tid = transcript_store.save(
            self.db,
            self.settings.transcripts_dir,
            transcript_id=p.get("transcript_id") or uuid.uuid4().hex[:12],
            stream_id=job["stream_id"],
            variant=job["variant"],
            course_name=job["course_name"],
            title=job["title"],
            media_path=str(media),
            engine=desc["engine"],
            model=desc["model"],
            language=detected_lang or p.get("language"),
            audio_seconds=duration,
            processing_seconds=stt_wall,
            segments=merged,
            folder=_transcript_folder(job),
        )
        self.db.execute(
            "INSERT INTO benchmarks (device, engine, model, compute_type, audio_seconds, wall_seconds, rtf, "
            "peak_mem_mb, notes, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                desc.get("device_label"),
                desc["engine"],
                desc["model"],
                desc.get("compute_type"),
                duration,
                stt_wall,
                stt_wall / duration if duration else None,
                peak,
                f"job {jid}",
                time.time(),
            ),
        )
        shutil.rmtree(work, ignore_errors=True)
        rtf = stt_wall / duration if duration else 0
        self._update(
            jid,
            status="completed",
            stage="complete",
            progress=1.0,
            finished_at=time.time(),
            output_path=tid,
            speed=None,
            message=f"{len(merged)} segments · {duration / 60:.1f} min audio in {stt_wall:.0f}s "
            f"(RTF {rtf:.3f}) · total {total_wall:.0f}s",
        )
        self.bus.publish("transcript", {"id": tid, "stream_id": job["stream_id"]})


def _transcript_folder(job: dict[str, Any]) -> str:
    if job.get("year") and job.get("term"):
        return f"{semester_folder(int(job['year']), job['term'])}/{sanitize_filename(job.get('course_name') or '')}"
    return "imported"


def _seg_to_dict(s: Segment) -> dict[str, Any]:
    return {
        "start": s.start,
        "end": s.end,
        "text": s.text,
        "words": [[w.start, w.end, w.word] for w in s.words],
    }


def _seg_from_dict(d: dict[str, Any]) -> Segment:
    return Segment(d["start"], d["end"], d["text"], [Word(a, b, c) for a, b, c in d.get("words", [])])
