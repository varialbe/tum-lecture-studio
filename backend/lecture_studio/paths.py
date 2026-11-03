"""Filesystem naming: safe, deterministic, cross-platform paths."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

_ILLEGAL = re.compile(r'[\/:*?"<>|\x00-\x1f]')
_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(name: str, max_len: int = 120, fallback: str = "untitled") -> str:
    """Make a string safe as a single path component on Windows, macOS and Linux."""
    name = unicodedata.normalize("NFC", name or "")
    name = re.sub(r"[\t\r\n]+", " ", name)
    name = _ILLEGAL.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = re.sub(r"_+", "_", name)
    name = name.strip(" .")  # Windows forbids trailing dots/spaces
    if not name:
        name = fallback
    if name.split(".")[0].upper() in _RESERVED:
        name = f"_{name}"
    if len(name.encode("utf-8")) > max_len:
        encoded = name.encode("utf-8")[:max_len]
        name = encoded.decode("utf-8", errors="ignore").rstrip(" .")
    return name


def semester_folder(year: int, term: str) -> str:
    return f"{year}-{'WiSe' if term == 'W' else 'SoSe'}"


def recording_path(
    root: Path,
    *,
    year: int,
    term: str,
    course_name: str,
    course_slug: str,
    stream_id: int,
    date: str | None,
    title: str,
    variant: str,
) -> Path:
    """<root>/<2026-WiSe>/<Course>/<2026-10-08 Title [COMB] #75911>.mp4

    The stream id keeps names unique even when lectures share a title.
    """
    course = sanitize_filename(course_name or course_slug, 80)
    day = (date or "")[:10]
    stem = sanitize_filename(f"{day} {title}".strip(), 100)
    return root / semester_folder(year, term) / course / f"{stem} [{variant}] #{stream_id}.mp4"
