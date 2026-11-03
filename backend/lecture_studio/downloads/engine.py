"""Download engines and media verification.

* `http_range_download` - TUM-Live's signed `download=1` URLs serve a faststart
  MP4 with `Accept-Ranges: bytes` and a real Content-Length, so progress is
  measured in bytes and interrupted downloads resume with a Range request.
  The edge server may take minutes to prepare the file before the first byte.
* `ffmpeg_hls_download` - fallback for HLS playlists (`-c copy`, no transcode);
  progress = muxed media time / known duration. HLS can't be resumed, so an
  interrupted download restarts.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..tumlive.client import USER_AGENT

ProgressCb = Callable[["Progress"], None]


class DownloadError(Exception):
    def __init__(self, message: str, transient: bool = True, expired: bool = False):
        super().__init__(message)
        self.transient = transient
        self.expired = expired  # signed URL / session no longer valid


@dataclass
class Progress:
    stage: str
    bytes_done: int = 0
    bytes_total: int | None = None
    fraction: float | None = None
    speed: float | None = None  # bytes/s


def which(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        raise DownloadError(f"{binary} not found on PATH", transient=False)
    return path


class _SpeedMeter:
    def __init__(self, window: float = 5.0):
        self.window = window
        self.samples: list[tuple[float, int]] = []

    def add(self, total_bytes: int) -> float | None:
        now = time.monotonic()
        self.samples.append((now, total_bytes))
        while self.samples and now - self.samples[0][0] > self.window:
            self.samples.pop(0)
        if len(self.samples) < 2:
            return None
        dt = self.samples[-1][0] - self.samples[0][0]
        return (self.samples[-1][1] - self.samples[0][1]) / dt if dt > 0 else None


async def http_range_download(
    url: str,
    dest: Path,
    on_progress: ProgressCb,
    *,
    first_byte_timeout: float = 600.0,
    transport: httpx.AsyncBaseTransport | None = None,
    chunk_size: int = 1 << 20,
) -> int:
    """Download `url` into `dest` (a .part file), resuming if it already has data.

    Returns the final size. Raises DownloadError on failure.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    have = dest.stat().st_size if dest.exists() else 0
    headers = {"User-Agent": USER_AGENT}
    if have:
        headers["Range"] = f"bytes={have}-"
    timeout = httpx.Timeout(connect=30.0, read=first_byte_timeout, write=30.0, pool=30.0)
    on_progress(Progress("preparing", bytes_done=have))
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, transport=transport) as http:
        try:
            async with http.stream("GET", url, headers=headers) as resp:
                if resp.status_code in (401, 403):
                    raise DownloadError(f"access denied (HTTP {resp.status_code})", expired=True)
                if resp.status_code == 416:  # range not satisfiable: we already have everything?
                    total = _total_from_content_range(resp.headers.get("content-range"))
                    if total is not None and have == total:
                        return have
                    dest.unlink(missing_ok=True)
                    raise DownloadError("resume offset rejected; restarting", transient=True)
                if resp.status_code >= 400:
                    raise DownloadError(f"HTTP {resp.status_code}", transient=resp.status_code >= 500)
                ctype = resp.headers.get("content-type", "")
                if "mpegurl" in ctype.lower():
                    raise DownloadError("server returned an HLS playlist, not a file", transient=False)
                if resp.status_code == 206:
                    total = _total_from_content_range(resp.headers.get("content-range"))
                    mode = "ab"
                else:  # 200: server ignored the Range header -> restart from zero
                    have = 0
                    cl = resp.headers.get("content-length")
                    total = int(cl) if cl and cl.isdigit() else None
                    mode = "wb"
                meter = _SpeedMeter()
                done = have
                last_emit = 0.0
                with open(dest, mode) as fh:
                    async for chunk in resp.aiter_bytes(chunk_size):
                        fh.write(chunk)
                        done += len(chunk)
                        now = time.monotonic()
                        if now - last_emit >= 0.5:
                            last_emit = now
                            on_progress(
                                Progress(
                                    "downloading",
                                    done,
                                    total,
                                    done / total if total else None,
                                    meter.add(done),
                                )
                            )
                on_progress(Progress("downloading", done, total, 1.0 if total else None, None))
        except httpx.TimeoutException as exc:
            raise DownloadError(f"timeout: {type(exc).__name__}") from exc
        except httpx.TransportError as exc:
            raise DownloadError(f"network error: {type(exc).__name__}") from exc
    size = dest.stat().st_size
    if total is not None and size != total:
        raise DownloadError(f"incomplete download: {size} of {total} bytes")
    return size


def _total_from_content_range(value: str | None) -> int | None:
    if not value:
        return None
    m = re.search(r"/(\d+)\s*$", value)
    return int(m.group(1)) if m else None


async def hls_duration(url: str, transport: httpx.AsyncBaseTransport | None = None) -> float | None:
    """Exact media length from an HLS playlist (sum of #EXTINF); follows one master-playlist hop."""
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, transport=transport) as http:
            text = (await http.get(url)).text
            if "#EXT-X-STREAM-INF" in text:
                variants = re.findall(r"#EXT-X-STREAM-INF:[^\n]*BANDWIDTH=(\d+)[^\n]*\n([^\n#]+)", text)
                if not variants:
                    return None
                best = max(variants, key=lambda v: int(v[0]))[1].strip()
                text = (await http.get(str(httpx.URL(url).join(best)))).text
    except httpx.HTTPError:
        return None
    if "#EXT-X-KEY:METHOD=SAMPLE-AES" in text or "KEYFORMAT=" in text:
        raise DownloadError("stream uses DRM encryption - not supported", transient=False)
    total = sum(float(x) for x in re.findall(r"#EXTINF:([\d.]+)", text))
    return total or None


def _ytdlp_available() -> bool:
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        return False
    return True


async def ytdlp_hls_download(url: str, dest: Path, on_progress: ProgressCb, *, fragments: int = 8) -> int:
    """Record an HLS stream with yt-dlp: parallel fragments, real fragment-based progress, and
    resume of partially downloaded fragments after an interruption. Output is remuxed to MP4
    without re-encoding (ffmpeg -c copy via yt-dlp's fixup)."""
    import yt_dlp

    dest.parent.mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_running_loop()
    cancelled = threading.Event()
    meter = _SpeedMeter()
    last = [0.0]
    stem = dest.with_suffix("")  # yt-dlp appends the extension itself

    def hook(d: dict) -> None:
        if cancelled.is_set():
            raise DownloadError("cancelled", transient=False)
        if d.get("status") != "downloading":
            return
        now = time.monotonic()
        if now - last[0] < 0.5:
            return
        last[0] = now
        idx, count = d.get("fragment_index"), d.get("fragment_count")
        frac = (idx / count) if idx and count else None
        done = int(d.get("downloaded_bytes") or 0)
        est = d.get("total_bytes") or d.get("total_bytes_estimate")
        pr = Progress("downloading", done, int(est) if est else None, frac, d.get("speed") or meter.add(done))
        loop.call_soon_threadsafe(on_progress, pr)

    opts = {
        "outtmpl": str(stem) + ".%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "concurrent_fragment_downloads": fragments,
        "continuedl": True,
        "retries": 10,
        "fragment_retries": 10,
        "http_headers": {"User-Agent": USER_AGENT},
        "progress_hooks": [hook],
        "merge_output_format": "mp4",
        "final_ext": "mp4",
        "postprocessors": [{"key": "FFmpegFixupM3u8"}],
        "noplaylist": True,
    }

    def run() -> None:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

    on_progress(Progress("preparing"))
    try:
        await asyncio.to_thread(run)
    except asyncio.CancelledError:
        cancelled.set()
        raise
    except DownloadError:
        raise
    except Exception as exc:  # yt-dlp's DownloadError & friends
        msg = str(exc)
        expired = any(s in msg for s in ("403", "401", "Forbidden", "Unauthorized"))
        raise DownloadError(f"yt-dlp: {msg[:300]}", expired=expired) from exc
    produced = [
        p
        # not glob(): lecture filenames contain "[COMB]", which glob reads as a character class
        for p in dest.parent.iterdir()
        if p.name.startswith(stem.name + ".")
        if p.suffix in (".mp4", ".ts", ".mkv") and not p.name.endswith((".part", ".ytdl"))
    ]
    if not produced:
        raise DownloadError("yt-dlp finished without an output file")
    out = max(produced, key=lambda p: p.stat().st_mtime)
    if out != dest:
        out.replace(dest)
    return dest.stat().st_size


async def ffmpeg_hls_download(
    url: str, dest: Path, on_progress: ProgressCb, *, duration: float | None
) -> int:
    ffmpeg = which("ffmpeg")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.unlink(missing_ok=True)  # HLS remux cannot resume
    args = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-loglevel",
        "error",
        "-progress",
        "pipe:1",
        "-nostats",
        "-user_agent",
        USER_AGENT,
        "-i",
        url,
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(dest),
    ]
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    meter = _SpeedMeter()
    stderr_tail: list[str] = []

    async def read_err() -> None:
        assert proc.stderr
        async for line in proc.stderr:
            stderr_tail.append(line.decode(errors="replace").strip())
            del stderr_tail[:-20]

    err_task = asyncio.create_task(read_err())
    state: dict[str, str] = {}
    try:
        assert proc.stdout
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").strip()
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            state[k] = v
            if k == "progress":
                out_us = _int(state.get("out_time_us")) or _int(state.get("out_time_ms"))
                size = _int(state.get("total_size")) or 0
                frac = min(1.0, out_us / 1e6 / duration) if (duration and out_us) else None
                on_progress(Progress("downloading", size, None, frac, meter.add(size)))
        rc = await proc.wait()
    except asyncio.CancelledError:
        proc.kill()
        await proc.wait()
        raise
    finally:
        await err_task
    if rc != 0:
        msg = " | ".join(stderr_tail[-3:]) or f"ffmpeg exited {rc}"
        expired = any(s in msg for s in ("403", "401", "Forbidden"))
        raise DownloadError(f"ffmpeg: {msg[:300]}", expired=expired)
    return dest.stat().st_size


def _int(v: str | None) -> int | None:
    try:
        return int(v) if v not in (None, "", "N/A") else None
    except ValueError:
        return None


@dataclass
class MediaInfo:
    duration: float
    has_video: bool
    has_audio: bool
    size: int


async def probe(path: Path) -> MediaInfo:
    ffprobe = which("ffprobe")
    proc = await asyncio.create_subprocess_exec(
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_type",
        "-of",
        "json",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise DownloadError(f"ffprobe failed: {err.decode(errors='replace')[:200]}", transient=False)
    data = json.loads(out or b"{}")
    kinds = {s.get("codec_type") for s in data.get("streams", [])}
    return MediaInfo(
        duration=float((data.get("format") or {}).get("duration") or 0.0),
        has_video="video" in kinds,
        has_audio="audio" in kinds,
        size=os.path.getsize(path),
    )


async def verify_media(
    path: Path, expected_duration: float | None, *, need_audio: bool = True
) -> tuple[MediaInfo, list[str]]:
    """Reject corrupt files (must probe, have audio, non-zero duration).

    The API's duration is the *scheduled* slot, so a shorter file is only a
    warning; byte-exact Content-Length is the integrity check for HTTP files.
    """
    info = await probe(path)
    warnings: list[str] = []
    if need_audio and not info.has_audio:
        raise DownloadError("file has no audio stream", transient=False)
    if info.duration <= 0:
        raise DownloadError("file has zero duration (corrupt or incomplete)")
    if expected_duration and info.duration < 0.75 * expected_duration:
        warnings.append(
            f"media is {info.duration / 60:.0f} min, scheduled slot was {expected_duration / 60:.0f} min"
        )
    return info, warnings
