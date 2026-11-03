from pathlib import Path

import pytest

from lecture_studio.paths import recording_path, sanitize_filename
from lecture_studio.tumlive.models import Course, Semester, match_priority


def test_parse_course(course_json):
    c = Course.from_api(course_json["course"])
    assert c.slug == "WiSe26_PcMAEG"
    assert c.semester.key == "2026W"
    assert c.downloads_enabled is True
    assert c.key == "2026W/WiSe26_PcMAEG"
    first = c.recordings[0]
    assert first.id == 75911
    assert first.name == "Vorkurs: Integralrechnung"
    assert first.duration == 6300
    assert first.available_variants == ["COMB", "PRES", "CAM"]
    assert sorted(first.downloadable_variants) == ["CAM", "COMB", "PRES"]
    assert first.download_for("PRES").url.startswith(
        "https://edge.live.rbg.tum.de/vod/75911/STREAM_VERSION_PRESENTATION"
    )
    assert first.is_vod
    assert first.date().year == 2026


def test_recording_without_downloads_and_planned(course_json):
    c = Course.from_api(course_json["course"])
    no_dl = next(r for r in c.recordings if r.id == 99999)
    assert no_dl.is_vod and no_dl.downloadable_variants == []
    planned = next(r for r in c.recordings if r.id == 99998)
    assert not planned.is_vod and planned.is_planned


def test_public_view_never_leaks_signed_urls(course_json):
    c = Course.from_api(course_json["course"])
    for r in c.recordings:
        pub = r.to_public()
        assert "jwt" not in str(pub) and "http" not in str(pub)
    assert c.to_public()["recording_count"] == 4


def test_semester():
    s = Semester.parse("2026w")
    assert (s.year, s.term, s.key, s.label) == (2026, "W", "2026W", "WiSe 2026/27")
    assert Semester.parse("2025S").label == "SoSe 2025"
    with pytest.raises(ValueError):
        Semester.parse("26W")


@pytest.mark.parametrize(
    "name,slug,ident,expected",
    [
        ("Diskrete Strukturen (IN0015)", "ds", "", "IN0015"),
        ("Einführung in die Rechnerarchitektur", "ERA", "", "IN0004"),
        ("Einführung in die Informatik und Programmieren", "EIDIP", "", "EIDIP"),
        # actual TUM-Live title, WiSe 2026/27
        ("Einführung in die Informatik und das Programmieren (CIT12300...)", "950975968", "", "EIDIP"),
        ("Diskrete Strukturen (IN0015)", "950940756", "", "IN0015"),
        ("Einführung in die Rechnerarchitektur (IN0004)", "era26w", "", "IN0004"),
        ("Advanced Topics of Software Engineering", "eidi", "", None),
        ("Grundlagen", "IN0004WS26", "", "IN0004"),
        ("Analysis für Informatik", "ana", "", None),
    ],
)
def test_priority_matching(name, slug, ident, expected):
    c = Course(1, name, slug, Semester(2026, "W"), ident, True, True, "loggedin")
    assert match_priority(c) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('Vorlesung 1: "Mengen" / Relationen?', "Vorlesung 1_ _Mengen_ _ Relationen_"),
        ("CON", "_CON"),
        ("trailing dots...", "trailing dots"),
        ("   ", "untitled"),
        ("tab\tand\nnewline", "tab and newline"),
        ("Übung: Äquivalenz*", "Übung_ Äquivalenz_"),
    ],
)
def test_sanitize_filename(raw, expected):
    assert sanitize_filename(raw) == expected


def test_sanitize_truncates_on_utf8_boundary():
    out = sanitize_filename("ä" * 200, max_len=51)
    assert len(out.encode("utf-8")) <= 51
    out.encode("utf-8").decode("utf-8")  # still valid


def test_recording_path_layout(tmp_path):
    p = recording_path(
        tmp_path,
        year=2026,
        term="W",
        course_name="Diskrete Strukturen",
        course_slug="ds",
        stream_id=42,
        date="2026-10-08T06:30:00Z",
        title="Induktion: Teil 1",
        variant="COMB",
    )
    assert p == tmp_path / "2026-WiSe" / "Diskrete Strukturen" / "2026-10-08 Induktion_ Teil 1 [COMB] #42.mp4"
    assert isinstance(p, Path)
