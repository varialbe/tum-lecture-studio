"""ffmpeg-based audio extraction, silence detection and chunk cutting."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from ..downloads.engine import which
from .chunking import Chunk, parse_silencedetect


class AudioError(Exception):
    pass


async def _run(args: list[str], on_line: Callable[[str], None] | None = None) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    err_lines: list[str] = []

    async def pump_err() -> None:
        assert proc.stderr
        async for raw in proc.stderr:
            err_lines.append(raw.decode(errors="replace"))

    async def pump_out() -> None:
        assert proc.stdout
        async for raw in proc.stdout:
            if on_line:
                on_line(raw.decode(errors="replace").strip())

    try:
        await asyncio.gather(pump_err(), pump_out())
        rc = await proc.wait()
    except asyncio.CancelledError:
        proc.kill()
        await proc.wait()
        raise
    return rc, "".join(err_lines)


async def extract_audio(
    media: Path,
    wav: Path,
    duration: float | None,
    on_progress: Callable[[float | None], None],
    *,
    start: float | None = None,
    seconds: float | None = None,
) -> None:
    """Decode to 16 kHz mono PCM WAV (Whisper's native input) with loudness-safe resampling."""
    ffmpeg = which("ffmpeg")
    wav.parent.mkdir(parents=True, exist_ok=True)
    tmp = wav.with_suffix(".tmp.wav")

    def on_line(line: str) -> None:
        if line.startswith("out_time_us=") and duration:
            try:
                on_progress(min(1.0, int(line.split("=", 1)[1]) / 1e6 / duration))
            except ValueError:
                pass

    clip: list[str] = []
    if start:
        clip += ["-ss", f"{start:.3f}"]
    if seconds:
        clip += ["-t", f"{seconds:.3f}"]
        duration = min(duration or seconds, seconds)
    rc, err = await _run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-loglevel",
            "error",
            "-progress",
            "pipe:1",
            *clip,
            "-i",
            str(media),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(tmp),
        ],
        on_line,
    )
    if rc != 0:
        tmp.unlink(missing_ok=True)
        raise AudioError(f"audio extraction failed: {err.strip()[-300:]}")
    tmp.replace(wav)


async def detect_silences(wav: Path, noise_db: int = -35, min_dur: float = 0.4) -> list[tuple[float, float]]:
    ffmpeg = which("ffmpeg")
    rc, err = await _run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-i",
            str(wav),
            "-af",
            f"silencedetect=noise={noise_db}dB:d={min_dur}",
            "-f",
            "null",
            "-",
        ]
    )
    if rc != 0:
        return []
    return parse_silencedetect(err)


async def cut_chunk(wav: Path, chunk: Chunk, dest: Path) -> None:
    ffmpeg = which("ffmpeg")
    rc, err = await _run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-loglevel",
            "error",
            "-ss",
            f"{chunk.audio_start:.3f}",
            "-t",
            f"{chunk.audio_duration:.3f}",
            "-i",
            str(wav),
            "-c:a",
            "pcm_s16le",
            str(dest),
        ]
    )
    if rc != 0:
        raise AudioError(f"chunk cut failed: {err.strip()[-200:]}")


async def wav_duration(wav: Path) -> float:
    # PCM s16 mono 16 kHz: exact from the file size (header is 44+ bytes).
    size = wav.stat().st_size
    return max(0.0, (size - 44) / (16000 * 2))
