"""Persist transcripts: DB rows (+ full-text index) and export files on disk."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..db import Database
from ..paths import sanitize_filename
from ..stt.base import Segment
from .export import EXPORTERS


def save(
    db: Database,
    root: Path,
    *,
    transcript_id: str,
    stream_id: int | None,
    variant: str | None,
    course_name: str | None,
    title: str | None,
    media_path: str,
    engine: str,
    model: str,
    language: str | None,
    audio_seconds: float,
    processing_seconds: float,
    segments: list[Segment],
    folder: str,
) -> str:
    now = time.time()
    db.execute("DELETE FROM segments WHERE transcript_id=?", (transcript_id,))
    db.execute("DELETE FROM segments_fts WHERE transcript_id=?", (transcript_id,))
    db.execute(
        "INSERT OR REPLACE INTO transcripts (id, stream_id, variant, course_name, title, media_path, engine, "
        "model, language, audio_seconds, processing_seconds, created_at, updated_at, edited) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
        (
            transcript_id,
            stream_id,
            variant,
            course_name,
            title,
            media_path,
            engine,
            model,
            language,
            audio_seconds,
            processing_seconds,
            now,
            now,
        ),
    )
    rows = [
        {
            "start": s.start,
            "end": s.end,
            "text": s.text,
            "words": [[round(w.start, 3), round(w.end, 3), w.word] for w in s.words] or None,
        }
        for s in segments
    ]
    _write_segments(db, transcript_id, rows)
    write_files(db, root, transcript_id, folder)
    return transcript_id


def _write_segments(db: Database, tid: str, rows: list[dict[str, Any]]) -> None:
    for i, s in enumerate(rows):
        words = json.dumps(s["words"], ensure_ascii=False) if s.get("words") else None
        db.execute(
            "INSERT INTO segments (transcript_id, idx, start, end, text, words) VALUES (?,?,?,?,?,?)",
            (tid, i, s["start"], s["end"], s["text"], words),
        )
        db.execute("INSERT INTO segments_fts (text, transcript_id, idx) VALUES (?,?,?)", (s["text"], tid, i))


def get(db: Database, tid: str) -> dict[str, Any] | None:
    t = db.query_one("SELECT * FROM transcripts WHERE id=?", (tid,))
    if not t:
        return None
    t["segments"] = db.query(
        "SELECT idx, start, end, text, words FROM segments WHERE transcript_id=? ORDER BY idx", (tid,)
    )
    for seg in t["segments"]:
        seg["words"] = json.loads(seg["words"]) if seg.get("words") else None
    return t


def meta(t: dict[str, Any]) -> dict[str, Any]:
    return {
        k: t.get(k)
        for k in (
            "id",
            "title",
            "course_name",
            "engine",
            "model",
            "language",
            "stream_id",
            "variant",
            "audio_seconds",
            "processing_seconds",
            "created_at",
            "edited",
        )
    }


def update_segments(db: Database, root: Path, tid: str, edits: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Apply text edits ({idx, text}) made in the viewer."""
    for e in edits:
        text = " ".join(str(e["text"]).split())
        # edited text no longer matches the word timings -> fall back to segment highlighting
        db.execute(
            "UPDATE segments SET text=?, words=NULL WHERE transcript_id=? AND idx=?",
            (text, tid, int(e["idx"])),
        )
        db.execute(
            "UPDATE segments_fts SET text=? WHERE transcript_id=? AND idx=?", (text, tid, int(e["idx"]))
        )
    db.execute("UPDATE transcripts SET edited=1, updated_at=? WHERE id=?", (time.time(), tid))
    t = get(db, tid)
    if t:
        existing = db.get_setting(f"transcript_folder:{tid}")
        write_files(db, root, tid, existing or "edited")
    return t


def write_files(db: Database, root: Path, tid: str, folder: str) -> Path | None:
    t = get(db, tid)
    if not t:
        return None
    db.set_setting(f"transcript_folder:{tid}", folder)
    out_dir = root / folder
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = sanitize_filename(f"{t.get('title') or 'transcript'} #{t.get('stream_id') or tid}", 110)
    for fmt, fn in EXPORTERS.items():
        (out_dir / f"{stem}.{fmt}").write_text(fn(t["segments"], meta=meta(t)), encoding="utf-8")
    return out_dir


def delete(db: Database, tid: str) -> None:
    db.execute("DELETE FROM segments_fts WHERE transcript_id=?", (tid,))
    db.execute("DELETE FROM segments WHERE transcript_id=?", (tid,))
    db.execute("DELETE FROM transcripts WHERE id=?", (tid,))


def search(db: Database, q: str, limit: int = 100) -> list[dict[str, Any]]:
    terms = [t for t in q.replace('"', " ").split() if t]
    if not terms:
        return []
    fts = " ".join(f'"{t}"*' for t in terms)
    return db.query(
        "SELECT f.transcript_id, f.idx, s.start, s.end, snippet(segments_fts, 0, '<mark>', '</mark>', '…', 16) "
        "AS snippet, t.title, t.course_name FROM segments_fts f JOIN segments s ON s.transcript_id=f.transcript_id "
        "AND s.idx=f.idx JOIN transcripts t ON t.id=f.transcript_id WHERE segments_fts MATCH ? "
        "ORDER BY rank LIMIT ?",
        (fts, limit),
    )
