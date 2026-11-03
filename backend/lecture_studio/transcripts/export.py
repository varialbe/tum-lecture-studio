"""Transcript export formats: txt, md, srt, vtt, json."""

from __future__ import annotations

import json
from typing import Any

FORMATS = {
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "srt": "application/x-subrip; charset=utf-8",
    "vtt": "text/vtt; charset=utf-8",
    "json": "application/json",
}


def _ts(seconds: float, sep: str = ",") -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def to_txt(segments: list[dict[str, Any]], **_: Any) -> str:
    """Paragraphs: a new paragraph after pauses longer than 2 s."""
    paras: list[list[str]] = [[]]
    prev_end = None
    for s in segments:
        if prev_end is not None and s["start"] - prev_end > 2.0 and paras[-1]:
            paras.append([])
        paras[-1].append(s["text"].strip())
        prev_end = s["end"]
    return "\n\n".join(" ".join(p) for p in paras if p) + "\n"


def to_md(segments: list[dict[str, Any]], meta: dict[str, Any] | None = None, **_: Any) -> str:
    meta = meta or {}
    lines = [f"# {meta.get('title') or 'Transcript'}", ""]
    info = []
    for k, label in (
        ("course_name", "Course"),
        ("model", "Model"),
        ("engine", "Engine"),
        ("language", "Language"),
    ):
        if meta.get(k):
            info.append(f"**{label}:** {meta[k]}")
    if info:
        lines += [" · ".join(info), ""]
    block_start = None
    block: list[str] = []
    for s in segments:  # one timestamped paragraph per ~minute
        if block_start is None:
            block_start = s["start"]
        block.append(s["text"].strip())
        if s["end"] - block_start >= 60:
            lines += [f"**[{_clock(block_start)}]** " + " ".join(block), ""]
            block, block_start = [], None
    if block:
        lines += [f"**[{_clock(block_start or 0)}]** " + " ".join(block), ""]
    return "\n".join(lines)


def to_srt(segments: list[dict[str, Any]], **_: Any) -> str:
    out = []
    for i, s in enumerate(segments, 1):
        out.append(f"{i}\n{_ts(s['start'])} --> {_ts(s['end'])}\n{s['text'].strip()}\n")
    return "\n".join(out)


def to_vtt(segments: list[dict[str, Any]], **_: Any) -> str:
    out = ["WEBVTT", ""]
    for s in segments:
        out.append(f"{_ts(s['start'], '.')} --> {_ts(s['end'], '.')}\n{s['text'].strip()}\n")
    return "\n".join(out)


def to_json(segments: list[dict[str, Any]], meta: dict[str, Any] | None = None, **_: Any) -> str:
    return json.dumps(
        {
            "meta": meta or {},
            "segments": [
                {"start": round(s["start"], 3), "end": round(s["end"], 3), "text": s["text"]}
                for s in segments
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


EXPORTERS = {"txt": to_txt, "md": to_md, "srt": to_srt, "vtt": to_vtt, "json": to_json}


def export(fmt: str, segments: list[dict[str, Any]], meta: dict[str, Any] | None = None) -> str:
    if fmt not in EXPORTERS:
        raise ValueError(f"unknown format {fmt}")
    return EXPORTERS[fmt](segments, meta=meta)
