"""Split long recordings into chunks at silences and merge chunk transcripts.

Every instant of the recording is *owned* by exactly one chunk ([own_start,
own_end)), and each chunk's audio extends `overlap` seconds beyond its owned
region on both sides so words at a boundary are heard in full. When merging,
a word is kept only by the chunk that owns its midpoint - no speech is lost
(every moment is owned) and nothing is duplicated (only one owner).
Cut points are placed in detected silences, so boundaries rarely fall inside
a word at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .base import Segment, Word


@dataclass
class Chunk:
    index: int
    own_start: float
    own_end: float
    audio_start: float
    audio_end: float

    @property
    def audio_duration(self) -> float:
        return self.audio_end - self.audio_start


_SIL_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SIL_END = re.compile(r"silence_end:\s*([\d.]+)")


def parse_silencedetect(log: str) -> list[tuple[float, float]]:
    """Parse ffmpeg `silencedetect` stderr into (start, end) intervals."""
    out: list[tuple[float, float]] = []
    start: float | None = None
    for line in log.splitlines():
        m = _SIL_START.search(line)
        if m:
            start = max(0.0, float(m.group(1)))
            continue
        m = _SIL_END.search(line)
        if m and start is not None:
            out.append((start, float(m.group(1))))
            start = None
    return out


def plan_chunks(
    duration: float,
    silences: list[tuple[float, float]],
    *,
    target: float = 600.0,
    window: float = 120.0,
    overlap: float = 1.5,
) -> list[Chunk]:
    """Choose cut points near every `target` seconds, preferring the longest silence
    within +-`window`; falls back to a hard cut at the target (overlap covers it)."""
    if duration <= 0:
        return []
    cuts: list[float] = []
    pos = 0.0
    while duration - pos > target + window / 2:
        ideal = pos + target
        candidates = [
            (e - s, (s + e) / 2)
            for s, e in silences
            if ideal - window <= (s + e) / 2 <= ideal + window and (s + e) / 2 > pos + 30
        ]
        cut = max(candidates)[1] if candidates else ideal
        cuts.append(cut)
        pos = cut
    bounds = [0.0, *cuts, duration]
    chunks = []
    for i in range(len(bounds) - 1):
        a, b = bounds[i], bounds[i + 1]
        chunks.append(
            Chunk(
                i,
                a,
                b,
                max(0.0, a - overlap if i else 0.0),
                min(duration, b + overlap if i < len(bounds) - 2 else duration),
            )
        )
    return chunks


def _mid(start: float, end: float) -> float:
    return (start + end) / 2


def own_segments(chunk: Chunk, segments: list[Segment], is_last: bool) -> list[Segment]:
    """Keep only the speech this chunk owns. `segments` use absolute timestamps."""

    def owned(t: float) -> bool:
        return chunk.own_start <= t < chunk.own_end or (is_last and t >= chunk.own_end)

    out: list[Segment] = []
    for seg in segments:
        if seg.words:
            kept = [w for w in seg.words if owned(_mid(w.start, w.end))]
            if not kept:
                continue
            if len(kept) == len(seg.words):
                out.append(seg)
            else:
                out.append(Segment(kept[0].start, kept[-1].end, _join_words(kept), kept))
        elif owned(_mid(seg.start, seg.end)):
            out.append(seg)
    return out


def _join_words(words: list[Word]) -> str:
    return " ".join("".join(w.word for w in words).split())


def merge_chunks(chunk_results: list[tuple[Chunk, list[Segment]]]) -> list[Segment]:
    """Merge per-chunk segments (timestamps relative to each chunk's audio) into one list."""
    merged: list[Segment] = []
    n = len(chunk_results)
    for i, (chunk, segs) in enumerate(sorted(chunk_results, key=lambda cr: cr[0].index)):
        absolute = [s.shifted(chunk.audio_start) for s in segs]
        merged.extend(own_segments(chunk, absolute, is_last=i == n - 1))
    merged.sort(key=lambda s: (s.start, s.end))
    return drop_repetitions(merged)


def drop_repetitions(segments: list[Segment], max_repeats: int = 2) -> list[Segment]:
    """Remove Whisper's classic hallucination loops (the same line repeated many times)."""
    out: list[Segment] = []
    run = 0
    for s in segments:
        if out and s.text.strip().lower() == out[-1].text.strip().lower():
            run += 1
            if run >= max_repeats:
                continue
        else:
            run = 0
        out.append(s)
    return out
