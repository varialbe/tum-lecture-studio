from lecture_studio.stt.base import Segment, Word, build_prompt
from lecture_studio.stt.chunking import (
    Chunk,
    drop_repetitions,
    merge_chunks,
    parse_silencedetect,
    plan_chunks,
)
from lecture_studio.stt.engines import parse_openai_verbose, result_to_openai
from lecture_studio.transcripts.export import export, to_srt, to_vtt


def test_parse_silencedetect():
    log = """[silencedetect @ 0x1] silence_start: 12.5
[silencedetect @ 0x1] silence_end: 13.4 | silence_duration: 0.9
[silencedetect @ 0x1] silence_start: -0.01
[silencedetect @ 0x1] silence_end: 0.5 | silence_duration: 0.5"""
    assert parse_silencedetect(log) == [(12.5, 13.4), (0.0, 0.5)]


def test_plan_chunks_prefers_longest_silence_near_target():
    sil = [(590.0, 590.5), (640.0, 643.0), (1250.0, 1251.0)]
    chunks = plan_chunks(1800, sil, target=600, window=120, overlap=1.5)
    assert [round(c.own_end, 1) for c in chunks[:-1]] == [641.5, 1250.5]
    # owned regions tile the whole recording without gaps or overlap
    assert chunks[0].own_start == 0 and chunks[-1].own_end == 1800
    for a, b in zip(chunks, chunks[1:], strict=False):
        assert a.own_end == b.own_start
        assert a.audio_end == a.own_end + 1.5 and b.audio_start == b.own_start - 1.5


def test_plan_chunks_short_and_no_silence():
    assert len(plan_chunks(300, [])) == 1
    c = plan_chunks(1300, [], target=600, window=60)
    assert [x.own_end for x in c] == [600, 1200, 1300]
    assert plan_chunks(0, []) == []


def _words(start: float, text: str, step: float = 0.5) -> list[Word]:
    return [Word(start + i * step, start + i * step + 0.4, " " + w) for i, w in enumerate(text.split())]


def test_merge_word_ownership_no_loss_no_duplicates():
    # Speech crosses the 10.0 s boundary: "eins zwei drei vier" from 8.0 to 9.9, "fünf sechs" at 10.0..10.9
    c0 = Chunk(0, 0.0, 10.0, 0.0, 11.5)
    c1 = Chunk(1, 10.0, 20.0, 8.5, 20.0)
    w0 = _words(8.0, "eins zwei drei vier fünf sechs")  # chunk 0 hears everything up to 11.5
    seg0 = Segment(8.0, 10.9, "eins zwei drei vier fünf sechs", w0)
    # chunk 1 audio starts at 8.5 -> relative times; it hears "zwei..sechs" plus "sieben"
    w1_abs = _words(8.5, "zwei drei vier fünf sechs sieben")
    seg1 = Segment(
        0.0,
        3.4,
        "zwei drei vier fünf sechs sieben",
        [Word(w.start - 8.5, w.end - 8.5, w.word) for w in w1_abs],
    )
    merged = merge_chunks([(c1, [seg1]), (c0, [seg0])])
    text = " ".join(s.text for s in merged)
    assert text == "eins zwei drei vier fünf sechs sieben"


def test_merge_segment_level_fallback_uses_midpoints():
    c0 = Chunk(0, 0, 10, 0, 12)
    c1 = Chunk(1, 10, 20, 8, 20)
    a = [Segment(0, 4, "A"), Segment(8, 11, "B")]  # B midpoint 9.5 -> owned by c0
    b = [Segment(0, 3, "B again"), Segment(3, 6, "C")]  # abs 8..11 (mid 9.5, dropped) and 11..14
    merged = merge_chunks([(c0, a), (c1, b)])
    assert [s.text for s in merged] == ["A", "B", "C"]
    assert merged[2].start == 11


def test_drop_repetitions():
    segs = [
        Segment(i, i + 1, t)
        for i, t in enumerate(["Hallo", "Danke.", "Danke.", "danke.", "Danke.", "Weiter"])
    ]
    assert [s.text for s in drop_repetitions(segs)] == ["Hallo", "Danke.", "Danke.", "Weiter"]


def test_openai_roundtrip():
    from lecture_studio.stt.base import TranscriptionResult

    res = TranscriptionResult([Segment(0, 1.5, "Hallo Welt", _words(0, "Hallo Welt"))], "de", 1.5)
    back = parse_openai_verbose(result_to_openai(res))
    assert back.language == "de" and back.segments[0].text == "Hallo Welt"
    assert len(back.segments[0].words) == 2


def test_prompt():
    assert build_prompt("") is None
    assert "RISC-V" in build_prompt("Java, RISC-V")


SEGS = [{"start": 0.0, "end": 1.25, "text": "Hallo"}, {"start": 3661.5, "end": 3662.0, "text": "Ende"}]


def test_srt_vtt_timestamps():
    srt = to_srt(SEGS)
    assert "1\n00:00:00,000 --> 00:00:01,250\nHallo" in srt
    assert "2\n01:01:01,500 --> 01:01:02,000\nEnde" in srt
    vtt = to_vtt(SEGS)
    assert vtt.startswith("WEBVTT") and "01:01:01.500 --> 01:01:02.000" in vtt


def test_other_exports():
    assert export("txt", SEGS) == "Hallo\n\nEnde\n"
    md = export("md", SEGS, {"title": "VL 1", "model": "fw-large-v3-turbo"})
    assert md.startswith("# VL 1") and "**[0:00:00]** Hallo" in md and "fw-large-v3-turbo" in md
    import json

    j = json.loads(export("json", SEGS, {"title": "x"}))
    assert j["segments"][1] == {"start": 3661.5, "end": 3662.0, "text": "Ende"}


def test_wer_cer():
    from lecture_studio.stt.wer import cer, wer

    assert wer("Die Bijektion ist injektiv.", "die bijektion ist injektiv") == 0.0
    assert wer("a b c d", "a x c") == 0.5  # one substitution + one deletion
    assert cer("abc", "abd") == 1 / 3
    assert wer("", "") == 0.0
