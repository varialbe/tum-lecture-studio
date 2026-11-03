"""SQLite persistence for jobs, the local library, transcripts and settings.

A single connection guarded by a lock is plenty for a local single-user app;
every statement is short, so calling it from the event loop is acceptable.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,                 -- download | transcribe
    status TEXT NOT NULL,               -- queued | running | completed | failed | cancelled
    stage TEXT NOT NULL,                -- see jobs.Stage
    stream_id INTEGER,
    course_slug TEXT,
    course_name TEXT,
    year INTEGER,
    term TEXT,
    title TEXT,
    variant TEXT,
    params TEXT NOT NULL DEFAULT '{}',
    progress REAL,                      -- 0..1, NULL = indeterminate
    bytes_done INTEGER NOT NULL DEFAULT 0,
    bytes_total INTEGER,
    speed REAL,                         -- bytes/s (download) or x realtime (transcribe)
    message TEXT,
    error TEXT,
    output_path TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS jobs_stream ON jobs(stream_id, kind);

CREATE TABLE IF NOT EXISTS recordings (
    stream_id INTEGER NOT NULL,
    variant TEXT NOT NULL,
    course_id INTEGER,
    course_slug TEXT,
    course_name TEXT,
    year INTEGER,
    term TEXT,
    title TEXT,
    start TEXT,
    duration REAL,
    path TEXT NOT NULL,
    size INTEGER,
    downloaded_at REAL NOT NULL,
    PRIMARY KEY (stream_id, variant)
);

CREATE TABLE IF NOT EXISTS transcripts (
    id TEXT PRIMARY KEY,
    stream_id INTEGER,
    variant TEXT,
    course_name TEXT,
    title TEXT,
    media_path TEXT,
    engine TEXT,
    model TEXT,
    language TEXT,
    audio_seconds REAL,
    processing_seconds REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    edited INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS segments (
    transcript_id TEXT NOT NULL REFERENCES transcripts(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    start REAL NOT NULL,
    end REAL NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (transcript_id, idx)
);

CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(
    text, transcript_id UNINDEXED, idx UNINDEXED, tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS favorites (
    course_key TEXT PRIMARY KEY,        -- "<year><term>/<slug>"
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS benchmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device TEXT,
    engine TEXT,
    model TEXT,
    compute_type TEXT,
    audio_seconds REAL,
    wall_seconds REAL,
    rtf REAL,
    peak_mem_mb REAL,
    notes TEXT,
    created_at REAL NOT NULL
);
"""

JOB_COLUMNS = [
    "id",
    "kind",
    "status",
    "stage",
    "stream_id",
    "course_slug",
    "course_name",
    "year",
    "term",
    "title",
    "variant",
    "params",
    "progress",
    "bytes_done",
    "bytes_total",
    "speed",
    "message",
    "error",
    "output_path",
    "attempts",
    "created_at",
    "updated_at",
    "started_at",
    "finished_at",
]


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(segments)").fetchall()}
        if "words" not in cols:  # v0.2: word timings for karaoke-style highlighting
            self._conn.execute("ALTER TABLE segments ADD COLUMN words TEXT")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- generic helpers -------------------------------------------------
    def execute(self, sql: str, args: tuple | list = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, args)

    def query(self, sql: str, args: tuple | list = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    def query_one(self, sql: str, args: tuple | list = ()) -> dict[str, Any] | None:
        rows = self.query(sql, args)
        return rows[0] if rows else None

    # ---- jobs --------------------------------------------------------------
    def create_job(self, kind: str, **fields: Any) -> dict[str, Any]:
        now = time.time()
        job: dict[str, Any] = {
            "id": uuid.uuid4().hex[:12],
            "kind": kind,
            "status": "queued",
            "stage": "queued",
            "params": "{}",
            "bytes_done": 0,
            "attempts": 0,
            "created_at": now,
            "updated_at": now,
        }
        if "params" in fields and not isinstance(fields["params"], str):
            fields["params"] = json.dumps(fields["params"])
        job.update(fields)
        cols = [c for c in JOB_COLUMNS if c in job]
        with self._lock:
            self._conn.execute(
                f"INSERT INTO jobs ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                [job[c] for c in cols],
            )
        return self.get_job(job["id"])  # type: ignore[return-value]

    def update_job(self, job_id: str, **fields: Any) -> dict[str, Any] | None:
        if "params" in fields and not isinstance(fields["params"], str):
            fields["params"] = json.dumps(fields["params"])
        fields["updated_at"] = time.time()
        bad = set(fields) - set(JOB_COLUMNS)
        if bad:
            raise ValueError(f"unknown job columns: {bad}")
        sets = ",".join(f"{k}=?" for k in fields)
        with self._lock:
            self._conn.execute(f"UPDATE jobs SET {sets} WHERE id=?", [*fields.values(), job_id])
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.query_one("SELECT * FROM jobs WHERE id=?", (job_id,))
        return _decode_job(row) if row else None

    def list_jobs(self, limit: int = 500) -> list[dict[str, Any]]:
        return [
            _decode_job(r)
            for r in self.query("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))
        ]

    def active_job_for(self, kind: str, stream_id: int, variant: str | None) -> dict[str, Any] | None:
        row = self.query_one(
            "SELECT * FROM jobs WHERE kind=? AND stream_id=? AND IFNULL(variant,'')=IFNULL(?, '') "
            "AND status IN ('queued','running') ORDER BY created_at DESC LIMIT 1",
            (kind, stream_id, variant),
        )
        return _decode_job(row) if row else None

    def delete_job(self, job_id: str) -> None:
        self.execute("DELETE FROM jobs WHERE id=?", (job_id,))

    # ---- recordings --------------------------------------------------------
    def upsert_recording(self, **r: Any) -> None:
        r.setdefault("downloaded_at", time.time())
        cols = list(r)
        self.execute(
            f"INSERT OR REPLACE INTO recordings ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [r[c] for c in cols],
        )

    def get_recording(self, stream_id: int, variant: str) -> dict[str, Any] | None:
        return self.query_one(
            "SELECT * FROM recordings WHERE stream_id=? AND variant=?", (stream_id, variant)
        )

    def recordings_for_streams(self, stream_ids: list[int]) -> list[dict[str, Any]]:
        if not stream_ids:
            return []
        q = ",".join("?" * len(stream_ids))
        return self.query(f"SELECT * FROM recordings WHERE stream_id IN ({q})", stream_ids)

    def list_recordings(self) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM recordings ORDER BY downloaded_at DESC")

    def delete_recording(self, stream_id: int, variant: str) -> None:
        self.execute("DELETE FROM recordings WHERE stream_id=? AND variant=?", (stream_id, variant))

    # ---- settings / favorites ---------------------------------------------
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.query_one("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        self.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, json.dumps(value)))

    def favorites(self) -> set[str]:
        return {r["course_key"] for r in self.query("SELECT course_key FROM favorites")}

    def set_favorite(self, key: str, on: bool) -> None:
        if on:
            self.execute("INSERT OR IGNORE INTO favorites VALUES (?, ?)", (key, time.time()))
        else:
            self.execute("DELETE FROM favorites WHERE course_key=?", (key,))


def _decode_job(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    try:
        row["params"] = json.loads(row.get("params") or "{}")
    except json.JSONDecodeError:
        row["params"] = {}
    return row
