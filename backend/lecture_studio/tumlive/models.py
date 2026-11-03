"""Typed views over TUM-Live API v2 JSON (protobuf JSON mapping, camelCase)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

VARIANTS = ("COMB", "PRES", "CAM")
_VERSION_TO_VARIANT = {
    "STREAM_VERSION_COMBINED": "COMB",
    "STREAM_VERSION_PRESENTATION": "PRES",
    "STREAM_VERSION_CAMERA": "CAM",
}
_FRIENDLY_TO_VARIANT = {"combined": "COMB", "presentation": "PRES", "camera": "CAM"}


@dataclass
class Semester:
    year: int
    term: str  # "W" or "S"

    @property
    def key(self) -> str:
        return f"{self.year}{self.term}"

    @property
    def label(self) -> str:
        if self.term == "W":
            return f"WiSe {self.year}/{str(self.year + 1)[-2:]}"
        return f"SoSe {self.year}"

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> Semester:
        return cls(year=int(d.get("year", 0)), term=str(d.get("teachingTerm", "")))

    @classmethod
    def parse(cls, key: str) -> Semester:
        m = re.fullmatch(r"(\d{4})([WS])", key.strip().upper())
        if not m:
            raise ValueError(f"invalid semester key {key!r}, expected e.g. 2026W")
        return cls(int(m.group(1)), m.group(2))


@dataclass
class Download:
    variant: str
    friendly_name: str
    url: str


@dataclass
class Recording:
    id: int
    course_id: int
    name: str
    description: str
    start: str | None
    end: str | None
    duration: int | None  # seconds
    recording: bool
    live_now: bool
    is_planned: bool
    is_coming_up: bool
    playlists: dict[str, str] = field(default_factory=dict)  # variant -> HLS url
    downloads: list[Download] = field(default_factory=list)

    @property
    def available_variants(self) -> list[str]:
        return [v for v in VARIANTS if v in self.playlists]

    @property
    def downloadable_variants(self) -> list[str]:
        return [d.variant for d in self.downloads]

    @property
    def is_vod(self) -> bool:
        return self.recording and not self.live_now and bool(self.playlists)

    def download_for(self, variant: str) -> Download | None:
        return next((d for d in self.downloads if d.variant == variant), None)

    def date(self) -> datetime | None:
        if not self.start or self.start.startswith("0001"):
            return None
        try:
            return datetime.fromisoformat(self.start.replace("Z", "+00:00"))
        except ValueError:
            return None

    @property
    def title(self) -> str:
        """Display name. Some lectures have no name on TUM-Live; fall back to the date."""
        if self.name:
            return self.name
        d = self.date()
        return f"Vorlesung vom {d:%d.%m.%Y}" if d else f"Vorlesung #{self.id}"

    def to_public(self) -> dict[str, Any]:
        """Serialisable view for the GUI - never includes signed URLs."""
        d = asdict(self)
        d.pop("playlists")
        d.pop("downloads")
        d["title"] = self.title
        d["variants"] = self.available_variants
        d["downloadable_variants"] = self.downloadable_variants
        d["is_vod"] = self.is_vod
        return d

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> Recording:
        playlists = {}
        for key, variant in (("playlistUrl", "COMB"), ("playlistUrlPres", "PRES"), ("playlistUrlCam", "CAM")):
            if d.get(key):
                playlists[variant] = d[key]
        downloads = []
        for dl in d.get("downloads") or []:
            url = dl.get("downloadUrl") or ""
            dl_variant = _variant_of(url, dl.get("friendlyName", ""))
            if url and dl_variant:
                downloads.append(
                    Download(variant=dl_variant, friendly_name=dl.get("friendlyName", ""), url=url)
                )
        dur = d.get("duration")
        return cls(
            id=int(d["id"]),
            course_id=int(d.get("courseId") or 0),
            name=(d.get("name") or "").strip(),
            description=d.get("description") or "",
            start=d.get("start"),
            end=d.get("end"),
            duration=int(dur) if dur else None,
            recording=bool(d.get("recording")),
            live_now=bool(d.get("liveNow")),
            is_planned=bool(d.get("isPlanned")),
            is_coming_up=bool(d.get("isComingUp")),
            playlists=playlists,
            downloads=downloads,
        )


def _variant_of(url: str, friendly: str) -> str | None:
    for version, variant in _VERSION_TO_VARIANT.items():
        if version in url:
            return variant
    return _FRIENDLY_TO_VARIANT.get(friendly.strip().lower())


@dataclass
class Course:
    id: int
    name: str
    slug: str
    semester: Semester
    tum_online_identifier: str
    vod_enabled: bool
    downloads_enabled: bool
    visibility: str
    pinned: bool = False
    source: set[str] = field(default_factory=set)  # enrolled | pinned | public | search
    recordings: list[Recording] = field(default_factory=list)
    last_recording: Recording | None = None

    @property
    def key(self) -> str:
        return f"{self.semester.key}/{self.slug}"

    @classmethod
    def from_api(cls, d: dict[str, Any], source: str | None = None) -> Course:
        sem = Semester.from_api(d.get("semester") or {})
        recs = [Recording.from_api(s) for s in d.get("streams") or [] if s.get("id")]
        last = d.get("lastRecording")
        return cls(
            id=int(d.get("id") or 0),
            name=(d.get("name") or "").strip(),
            slug=d.get("slug") or "",
            semester=sem,
            tum_online_identifier=d.get("tumOnlineIdentifier") or "",
            vod_enabled=bool(d.get("vodEnabled")),
            downloads_enabled=bool(d.get("downloadsEnabled")),
            visibility=d.get("visibility") or "",
            pinned=bool(d.get("pinned")),
            source={source} if source else set(),
            recordings=recs,
            last_recording=Recording.from_api(last) if last and last.get("id") else None,
        )

    def to_public(self, favorites: set[str] | None = None) -> dict[str, Any]:
        vods = [r for r in self.recordings if r.is_vod]
        return {
            "key": self.key,
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "year": self.semester.year,
            "term": self.semester.term,
            "semester_label": self.semester.label,
            "tum_online_identifier": self.tum_online_identifier,
            "vod_enabled": self.vod_enabled,
            "downloads_enabled": self.downloads_enabled,
            "visibility": self.visibility,
            "pinned": self.pinned,
            "source": sorted(self.source),
            "recording_count": len(vods) if self.recordings else None,
            "favorite": bool(favorites and self.key in favorites),
            "priority": match_priority(self),
        }


# Priority courses requested by the user. Matched by name/slug/identifier against
# what TUM-Live actually reports - no identifiers are invented.
PRIORITY_COURSES: list[dict[str, Any]] = [
    {
        "id": "IN0015",
        "label": "Diskrete Strukturen",
        "patterns": [r"diskrete\s+strukturen", r"(?<![A-Za-z0-9])IN0?015(?!\d)"],
    },
    {
        "id": "IN0004",
        "label": "Einführung in die Rechnerarchitektur",
        "patterns": [r"rechnerarchitektur", r"(?<![A-Za-z0-9])IN0?004(?!\d)", r"\bERA\b"],
    },
    {
        "id": "EIDIP",
        "label": "Einführung in die Informatik und Programmieren",
        "patterns": [r"informatik\s+und\s+(das\s+)?programmieren", r"\bEIDIP\b", r"\bEIP\b"],
    },
]


def match_priority(course: Course) -> str | None:
    hay = " ".join([course.name, course.slug, course.tum_online_identifier])
    for p in PRIORITY_COURSES:
        if any(re.search(pat, hay, re.IGNORECASE) for pat in p["patterns"]):
            return p["id"]
    return None


PRIORITY_SEARCH_TERMS = ["Diskrete Strukturen", "Rechnerarchitektur", "Informatik und das Programmieren"]
