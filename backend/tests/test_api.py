"""API surface: security guards and offline endpoints (no network)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from lecture_studio.main import create_app
from lecture_studio.stt.base import Segment
from lecture_studio.transcripts import store as tstore

H = {"X-Studio": "1"}


def test_health_and_guards(settings):
    with TestClient(create_app(settings)) as c:
        assert c.get("/api/health").json()["ok"] is True
        # mutating requests without the custom header are rejected (CSRF guard)
        assert c.post("/api/session/logout").status_code == 403
        assert c.post("/api/session/logout", headers=H).status_code == 200
        # DNS-rebinding guard
        assert c.get("/api/health", headers={"host": "evil.example"}).status_code == 403
        assert c.get("/api/health", headers={"host": "localhost:8420"}).status_code == 200


def test_session_anonymous_and_settings(settings):
    with TestClient(create_app(settings)) as c:
        assert c.get("/api/session").json()["state"] == "anonymous"
        sys = c.get("/api/system").json()
        assert sys["recommendation"]["model"]
        r = c.put(
            "/api/settings", json={"values": {"stt.language": "en", "auto_transcribe": True}}, headers=H
        )
        assert r.status_code == 200 and r.json()["settings"]["language"] == "en"
        assert r.json()["settings"]["auto_transcribe"] is True
        assert c.put("/api/settings", json={"values": {"evil": 1}}, headers=H).status_code == 400
        assert c.put("/api/settings", json={"values": {"stt.model": "nope"}}, headers=H).status_code == 400
        models = c.get("/api/models").json()["models"]
        assert {m["id"] for m in models} >= {"fw-large-v3-turbo", "mlx-large-v3-turbo"}
        assert all("installed" in m for m in models)


def test_transcript_endpoints(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        db = app.state.studio.db
        tid = tstore.save(
            db,
            settings.transcripts_dir,
            transcript_id="t1",
            stream_id=5,
            variant="COMB",
            course_name="DS",
            title="Induktion",
            media_path="/none",
            engine="fake",
            model="m",
            language="de",
            audio_seconds=10,
            processing_seconds=1,
            segments=[Segment(0, 2, "Vollständige Induktion"), Segment(2, 4, "über Bijektion")],
            folder="x",
        )
        assert c.get("/api/transcripts").json()["transcripts"][0]["segment_count"] == 2
        hits = c.get("/api/transcripts/search", params={"q": "bijekt"}).json()["hits"]
        assert hits and hits[0]["idx"] == 1 and "<mark>" in hits[0]["snippet"]
        # diacritics-insensitive search
        assert c.get("/api/transcripts/search", params={"q": "uber"}).json()["hits"]
        srt = c.get(f"/api/transcripts/{tid}/export/srt")
        assert srt.status_code == 200 and "00:00:02,000 --> 00:00:04,000" in srt.text
        assert "attachment" in srt.headers["content-disposition"]
        r = c.patch(
            f"/api/transcripts/{tid}", json={"edits": [{"idx": 1, "text": "über   Bijektionen"}]}, headers=H
        )
        assert r.json()["segments"][1]["text"] == "über Bijektionen" and r.json()["edited"] == 1
        assert c.get("/api/transcripts/search", params={"q": "Bijektionen"}).json()["hits"]
        assert c.get(f"/api/transcripts/{tid}/export/exe").status_code == 404
        assert c.delete(f"/api/transcripts/{tid}", headers=H).json()["ok"]
        assert c.get(f"/api/transcripts/{tid}").status_code == 404


def test_transcribe_requires_download(settings):
    with TestClient(create_app(settings)) as c:
        r = c.post("/api/transcribe", json={"items": [{"stream_id": 1, "variant": "COMB"}]}, headers=H)
        assert r.json()["results"][0]["error"] == "recording is not downloaded"
