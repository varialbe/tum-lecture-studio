"""Download + transcription job lifecycle with real files and ffmpeg, mocked network/STT."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from lecture_studio.db import Database
from lecture_studio.downloads.engine import DownloadError, Progress, http_range_download
from lecture_studio.events import EventBus
from lecture_studio.jobs import JobManager
from lecture_studio.stt.base import Segment, TranscriptionResult, Word
from lecture_studio.transcripts import store as tstore
from lecture_studio.tumlive.models import Download, Recording

from .conftest import needs_ffmpeg


def range_server(data: bytes, fail_after: int | None = None, counter: dict | None = None):
    """Mock edge server: supports Range; optionally drops the connection after N bytes once."""

    def handler(req: httpx.Request) -> httpx.Response:
        if counter is not None:
            counter["requests"] = counter.get("requests", 0) + 1
            counter.setdefault("ranges", []).append(req.headers.get("range"))
        start = 0
        m = re.match(r"bytes=(\d+)-", req.headers.get("range", ""))
        if m:
            start = int(m.group(1))
        body = data[start:]
        headers = {"content-type": "video/mp4", "accept-ranges": "bytes"}
        status = 200
        if m:
            status = 206
            headers["content-range"] = f"bytes {start}-{len(data) - 1}/{len(data)}"
        if fail_after is not None and counter is not None and counter["requests"] == 1:

            def gen():
                yield body[:fail_after]
                raise httpx.ReadError("connection reset")

            return httpx.Response(status, headers=headers, stream=_Stream(gen()))
        headers["content-length"] = str(len(body))
        return httpx.Response(status, headers=headers, content=body)

    return httpx.MockTransport(handler)


class _Stream(httpx.AsyncByteStream):
    def __init__(self, it):
        self.it = it

    async def __aiter__(self):
        for chunk in self.it:
            yield chunk


async def test_range_download_resumes_after_interruption(tmp_path):
    data = bytes(range(256)) * 4096  # 1 MiB
    counter: dict[str, Any] = {}
    transport = range_server(data, fail_after=300_000, counter=counter)
    dest = tmp_path / "x.part"
    seen: list[Progress] = []
    with pytest.raises(DownloadError):
        await http_range_download("https://edge/x", dest, seen.append, transport=transport, chunk_size=65536)
    partial = dest.stat().st_size
    assert 0 < partial < len(data)
    size = await http_range_download(
        "https://edge/x", dest, seen.append, transport=transport, chunk_size=65536
    )
    assert size == len(data) and dest.read_bytes() == data
    assert counter["ranges"] == [None, f"bytes={partial}-"]  # genuinely resumed, not restarted
    assert seen[-1].fraction == 1.0 and seen[-1].bytes_total == len(data)


async def test_range_download_restarts_if_server_ignores_range(tmp_path):
    data = b"a" * 1000

    def handler(req):  # always 200 full body
        return httpx.Response(200, content=data, headers={"content-length": "1000"})

    dest = tmp_path / "y.part"
    dest.write_bytes(b"garbage")
    await http_range_download("https://edge/y", dest, lambda p: None, transport=httpx.MockTransport(handler))
    assert dest.read_bytes() == data


async def test_range_download_access_denied_is_expired(tmp_path):
    from lecture_studio.downloads.engine import DownloadError

    with pytest.raises(DownloadError) as ei:
        await http_range_download(
            "https://edge/z",
            tmp_path / "z",
            lambda p: None,
            transport=httpx.MockTransport(lambda r: httpx.Response(403)),
        )
    assert ei.value.expired


# ---- job manager --------------------------------------------------------------
class FakeTum:
    def __init__(self, rec: Recording):
        self.rec = rec
        self.calls = 0

    async def fresh_recording(self, slug, sem, sid):
        self.calls += 1
        return self.rec if sid == self.rec.id else None


class FakeEngine:
    engine = "fake"
    model = "fake-model"

    def transcribe(self, audio: Path, opts, on_progress) -> TranscriptionResult:
        on_progress(0.5)
        words = [Word(0.5, 1.0, " Hallo"), Word(1.1, 1.6, " Welt")]
        on_progress(1.0)
        return TranscriptionResult([Segment(0.5, 1.6, "Hallo Welt", words)], "de", 8.0)


class FakeStt:
    def job_params(self, options):
        return {"model": "fake", "language": "de", "vocabulary": "Java", **options}

    async def engine_for(self, params, report):
        return FakeEngine(), {
            "engine": "fake",
            "model": "fake-model",
            "device": "cpu",
            "device_label": "test",
            "compute_type": "int8",
        }


COURSE = {"id": 1, "name": "Diskrete Strukturen", "slug": "ds", "year": 2026, "term": "W"}


def _rec(url="https://edge/vod/1/STREAM_VERSION_COMBINED/playlist.m3u8?jwt=x&download=1") -> Recording:
    return Recording(
        id=4242,
        course_id=1,
        name="Induktion",
        description="",
        start="2026-10-08T06:30:00Z",
        end=None,
        duration=8,
        recording=True,
        live_now=False,
        is_planned=False,
        is_coming_up=False,
        playlists={"COMB": url},
        downloads=[Download("COMB", "Combined", url)],
    )


async def _wait(db: Database, job_id: str, statuses=("completed", "failed", "cancelled"), timeout=60) -> dict:
    for _ in range(int(timeout * 20)):
        job = db.get_job(job_id)
        if job["status"] in statuses:
            return job
        await asyncio.sleep(0.05)
    raise AssertionError(f"job stuck: {db.get_job(job_id)}")


@needs_ffmpeg
async def test_download_then_transcribe_pipeline(settings, sample_mp4):
    db = Database(settings.db_path)
    bus = EventBus()
    events = bus.subscribe()
    mgr = JobManager(db, bus, settings, FakeTum(_rec()), FakeStt())
    mgr.download_transport = range_server(sample_mp4.read_bytes())

    res = mgr.enqueue_download(
        course=COURSE, recording=_rec().to_public(), variant="COMB", auto_transcribe=True
    )
    job = await _wait(db, res["job"]["id"])
    assert job["status"] == "completed", job
    assert job["stage"] == "download_complete"
    out = Path(job["output_path"])
    assert out.exists() and out.parent.name == "Diskrete Strukturen" and out.parent.parent.name == "2026-WiSe"
    assert not out.with_name(out.name + ".part").exists()
    assert db.get_recording(4242, "COMB")["size"] == sample_mp4.stat().st_size

    # duplicate detection
    assert (
        mgr.enqueue_download(course=COURSE, recording=_rec().to_public(), variant="COMB")["duplicate"]
        == "downloaded"
    )

    # auto-transcribe kicked in
    tjobs = [j for j in db.list_jobs() if j["kind"] == "transcribe"]
    assert len(tjobs) == 1
    tjob = await _wait(db, tjobs[0]["id"])
    assert tjob["status"] == "completed", tjob
    t = tstore.get(db, tjob["output_path"])
    assert [s["text"] for s in t["segments"]] == ["Hallo Welt"]
    assert t["model"] == "fake-model" and t["language"] == "de"
    exported = list(settings.transcripts_dir.rglob("*.srt"))
    assert exported and "Hallo Welt" in exported[0].read_text(encoding="utf-8")
    assert tstore.search(db, "welt")[0]["transcript_id"] == t["id"]
    assert db.query("SELECT COUNT(*) n FROM benchmarks")[0]["n"] == 1

    # stage events were streamed
    stages = []
    while not events.empty():
        msg = events.get_nowait()
        m = re.search(r'"stage": "(\w+)"', msg)
        if m:
            stages.append(m.group(1))
    for s in (
        "authenticating",
        "preparing",
        "downloading",
        "verifying",
        "download_complete",
        "extracting_audio",
        "transcribing",
        "exporting",
        "complete",
    ):
        assert s in stages, (s, stages)
    db.close()


@needs_ffmpeg
async def test_corrupt_download_fails_after_retries(settings, monkeypatch):
    import lecture_studio.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "MAX_ATTEMPTS", 2)
    monkeypatch.setattr(jobs_mod, "RETRY_BASE_SECONDS", 0.0)
    db = Database(settings.db_path)
    mgr = JobManager(db, EventBus(), settings, FakeTum(_rec()), FakeStt())
    mgr.download_transport = range_server(b"\x00" * 5000)  # not a video
    res = mgr.enqueue_download(
        course=COURSE, recording=_rec().to_public(), variant="COMB", auto_transcribe=False
    )
    job = await _wait(db, res["job"]["id"])
    assert job["status"] == "failed" and "invalid" in job["error"]
    assert job["attempts"] == 2
    assert db.get_recording(4242, "COMB") is None
    db.close()


async def test_downloads_not_permitted(settings):
    db = Database(settings.db_path)
    rec = _rec()
    rec.downloads = []
    mgr = JobManager(db, EventBus(), settings, FakeTum(rec), FakeStt())
    res = mgr.enqueue_download(
        course=COURSE, recording=rec.to_public(), variant="COMB", auto_transcribe=False
    )
    job = await _wait(db, res["job"]["id"])
    assert job["status"] == "failed" and "not enabled" in job["error"]
    db.close()


async def test_cancel_and_restart_recovery(settings):
    db = Database(settings.db_path)

    class SlowTum(FakeTum):
        async def fresh_recording(self, slug, sem, sid):
            await asyncio.sleep(30)

    mgr = JobManager(db, EventBus(), settings, SlowTum(_rec()), FakeStt())
    a = mgr.enqueue_download(
        course=COURSE, recording=_rec().to_public(), variant="COMB", auto_transcribe=False
    )
    dup = mgr.enqueue_download(course=COURSE, recording=_rec().to_public(), variant="COMB")
    assert dup["duplicate"] == "queued"
    await asyncio.sleep(0.2)
    assert db.get_job(a["job"]["id"])["stage"] == "authenticating"
    cancelled = await mgr.cancel(a["job"]["id"])
    assert cancelled["status"] == "cancelled"

    # Simulate a crash while a job was running, then restart the manager.
    b = db.create_job(
        "download",
        stream_id=4242,
        course_slug="ds",
        course_name="DS",
        year=2026,
        term="W",
        title="Induktion",
        variant="PRES",
        params={},
    )
    db.update_job(b["id"], status="running", stage="downloading", progress=0.4)
    mgr2 = JobManager(db, EventBus(), settings, SlowTum(_rec()), FakeStt())
    await mgr2.start()
    await asyncio.sleep(0.2)
    job = db.get_job(b["id"])
    assert job["status"] == "running" and job["attempts"] == 1  # re-queued and picked up again
    await mgr2.shutdown()
    assert db.get_job(b["id"])["status"] == "running"  # shutdown != user cancel -> resumes next start
    db.close()
