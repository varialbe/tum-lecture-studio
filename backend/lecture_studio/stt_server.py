"""Host-native STT companion service.

Docker containers on macOS cannot reach the Apple GPU (no Metal passthrough),
so on a Mac the containerised app sends audio chunks to this small server
running natively on the host, where mlx-whisper uses Metal. It works the same
with faster-whisper (e.g. native Windows + CUDA without NVIDIA Container
Toolkit).

API (OpenAI-compatible subset):
  POST /v1/audio/transcriptions  multipart: file, model?, language?, prompt?,
       response_format=verbose_json, timestamp_granularities[]=word|segment
  GET  /v1/models, GET /health

Run: lecture-studio-stt --host 127.0.0.1 --port 8421 [--model mlx-large-v3-turbo]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse

from .stt import catalog
from .stt.base import TranscribeOptions
from .stt.engines import FasterWhisperEngine, MlxWhisperEngine, result_to_openai
from .stt.hardware import detect

log = logging.getLogger("lecture_studio.stt_server")


def build_app(model_id: str | None, models_dir: Path, compute_type: str | None) -> FastAPI:
    hw = detect()
    rec_model, rec_ct, _ = catalog.recommend(hw, remote_configured=False)
    model_id = model_id or rec_model
    spec = catalog.get_spec(model_id)
    store = catalog.ModelStore(models_dir)
    lock = asyncio.Lock()
    app = FastAPI(title="TUM Lecture Studio STT companion")
    engine_holder: dict[str, Any] = {}

    def engine() -> Any:
        if "e" not in engine_holder:
            if not store.installed(spec):
                log.info("downloading %s ...", spec.repo)
                store.download(spec)
            path = store.local_path(spec)
            assert path
            if spec.engine == "mlx-whisper":
                engine_holder["e"] = MlxWhisperEngine(path, spec.id)
            else:
                device = "cuda" if hw.cuda_usable else "cpu"
                ct = compute_type or (rec_ct if device == "cuda" else "int8")
                engine_holder["e"] = FasterWhisperEngine(path, spec.id, device=device, compute_type=ct)
        return engine_holder["e"]

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "model": spec.id,
            "engine": spec.engine,
            "installed": store.installed(spec),
            "apple_silicon": hw.apple_silicon,
            "cuda": hw.cuda_usable,
        }

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        return {"object": "list", "data": [{"id": spec.id, "object": "model", "owned_by": "local"}]}

    @app.post("/v1/audio/transcriptions")
    async def transcriptions(
        file: UploadFile = File(...),
        model: str = Form("default"),
        language: str | None = Form(None),
        prompt: str | None = Form(None),
        response_format: str = Form("verbose_json"),
        granularities: list[str] = Form(default=[], alias="timestamp_granularities[]"),
    ) -> Any:
        suffix = Path(file.filename or "audio.wav").suffix or ".wav"
        fd, tmp = tempfile.mkstemp(suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as fh:
                while chunk := await file.read(1 << 20):
                    fh.write(chunk)
            opts = TranscribeOptions(
                language=language or None,
                initial_prompt=prompt,
                word_timestamps="word" in granularities or not granularities,
            )
            async with lock:  # one inference at a time keeps memory predictable
                t0 = time.time()
                try:
                    res = await asyncio.to_thread(engine().transcribe, Path(tmp), opts, lambda _f: None)
                except Exception as exc:  # noqa: BLE001
                    raise HTTPException(500, f"transcription failed: {exc}") from exc
                log.info("transcribed %.1fs audio in %.1fs", res.duration, time.time() - t0)
        finally:
            Path(tmp).unlink(missing_ok=True)
        body = result_to_openai(res)
        if response_format == "text":
            return PlainTextResponse(body["text"])
        if response_format == "json":
            return {"text": body["text"]}
        return body

    return app


def run() -> None:
    import uvicorn

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=os.environ.get("TLS_STT_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("TLS_STT_PORT", "8421")))
    ap.add_argument("--model", default=os.environ.get("TLS_STT_MODEL"))
    ap.add_argument("--compute-type", default=None)
    ap.add_argument("--models-dir", default=os.environ.get("TLS_MODELS_DIR", "./data/models"))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app = build_app(args.model, Path(args.models_dir).resolve(), args.compute_type)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    run()
