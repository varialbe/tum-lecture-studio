"""Concrete STT engine adapters."""

from __future__ import annotations

import importlib
import json
import threading
from pathlib import Path
from typing import Any

import httpx

from .base import ProgressFn, Segment, SttUnavailable, TranscribeOptions, TranscriptionResult, Word
from .hardware import register_cuda_dlls


def _clean(text: str) -> str:
    return " ".join(text.split())


class FasterWhisperEngine:
    """CTranslate2 Whisper - CUDA on NVIDIA, int8 on CPU."""

    engine = "faster-whisper"
    _cache: dict[tuple[str, str, str], Any] = {}
    _cache_lock = threading.Lock()

    def __init__(
        self,
        model_path: str,
        model_id: str,
        device: str = "auto",
        compute_type: str = "default",
        cpu_threads: int = 0,
    ):
        self.model_path = model_path
        self.model = model_id
        self.device = device
        self.compute_type = compute_type
        self.cpu_threads = cpu_threads

    def _load(self) -> Any:
        try:
            register_cuda_dlls()
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise SttUnavailable("faster-whisper is not installed (pip install '.[whisper]')") from exc
        key = (self.model_path, self.device, self.compute_type)
        with self._cache_lock:
            if key not in self._cache:
                self._cache.clear()  # keep one model resident; frees VRAM
                self._cache[key] = WhisperModel(
                    self.model_path,
                    device=self.device,
                    compute_type=self.compute_type,
                    cpu_threads=self.cpu_threads,
                )
            return self._cache[key]

    def transcribe(
        self, audio: Path, opts: TranscribeOptions, on_progress: ProgressFn
    ) -> TranscriptionResult:
        model = self._load()
        segments_iter, info = model.transcribe(
            str(audio),
            language=opts.language or None,
            beam_size=opts.beam_size,
            vad_filter=opts.vad,
            vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 300},
            word_timestamps=opts.word_timestamps,
            initial_prompt=opts.initial_prompt,
            condition_on_previous_text=False,  # avoids runaway repetition loops on long lectures
            hallucination_silence_threshold=2.0 if opts.word_timestamps else None,
        )
        duration = float(info.duration or 0.0)
        out: list[Segment] = []
        for s in segments_iter:
            words = [Word(float(w.start), float(w.end), w.word) for w in (s.words or [])]
            text = _clean(s.text)
            if text:
                out.append(Segment(float(s.start), float(s.end), text, words))
            if duration:
                on_progress(min(1.0, float(s.end) / duration))
        on_progress(1.0)
        return TranscriptionResult(out, info.language, duration)


class MlxWhisperEngine:
    """mlx-whisper on Apple Silicon (Metal). Native macOS only - not inside Docker."""

    engine = "mlx-whisper"
    _lock = threading.Lock()

    def __init__(self, model_path: str, model_id: str):
        self.model_path = model_path
        self.model = model_id

    def transcribe(
        self, audio: Path, opts: TranscribeOptions, on_progress: ProgressFn
    ) -> TranscriptionResult:
        try:
            import mlx_whisper

            # Not `import mlx_whisper.transcribe as mt`: the package re-exports the
            # `transcribe` function under that name, shadowing the submodule.
            mt = importlib.import_module("mlx_whisper.transcribe")
        except ImportError as exc:
            raise SttUnavailable("mlx-whisper is not installed (pip install '.[mlx]', macOS arm64)") from exc

        # mlx-whisper reports progress through tqdm (frames); hook it for real progress.
        orig_tqdm = mt.tqdm.tqdm

        class _Hook(orig_tqdm):  # type: ignore[misc, valid-type]
            # mlx-whisper passes disable=True (verbose=False), and a disabled tqdm
            # never advances self.n, so count frames ourselves.
            _done = 0.0

            def update(self, n: float | None = 1) -> bool | None:
                r = super().update(n)
                self._done += n or 0
                if self.total:
                    on_progress(min(1.0, self._done / self.total))
                return r

        with self._lock:  # tqdm patch is module-global
            mt.tqdm.tqdm = _Hook
            try:
                result = mlx_whisper.transcribe(
                    str(audio),
                    path_or_hf_repo=self.model_path,
                    language=opts.language or None,
                    initial_prompt=opts.initial_prompt,
                    word_timestamps=opts.word_timestamps,
                    condition_on_previous_text=False,
                    hallucination_silence_threshold=2.0 if opts.word_timestamps else None,
                    verbose=False,
                )
            finally:
                mt.tqdm.tqdm = orig_tqdm
        on_progress(1.0)
        segs = []
        for s in result.get("segments", []):
            text = _clean(s.get("text", ""))
            if not text:
                continue
            words = [Word(float(w["start"]), float(w["end"]), w["word"]) for w in s.get("words", []) or []]
            segs.append(Segment(float(s["start"]), float(s["end"]), text, words))
        dur = segs[-1].end if segs else 0.0
        return TranscriptionResult(segs, result.get("language"), dur)


class RemoteEngine:
    """OpenAI-compatible `/v1/audio/transcriptions` (our companion, vllm-metal, speaches, ...)."""

    engine = "remote"

    def __init__(self, base_url: str, model: str = "default", timeout: float = 3600.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def transcribe(
        self, audio: Path, opts: TranscribeOptions, on_progress: ProgressFn
    ) -> TranscriptionResult:
        grans = ["segment", "word"] if opts.word_timestamps else ["segment"]
        data: dict[str, Any] = {
            "model": self.model,
            "response_format": "verbose_json",
            "timestamp_granularities[]": grans,
        }
        if opts.language:
            data["language"] = opts.language
        if opts.initial_prompt:
            data["prompt"] = opts.initial_prompt
        url = (
            self.base_url
            if self.base_url.endswith("/transcriptions")
            else f"{self.base_url}/v1/audio/transcriptions"
        )
        try:
            with open(audio, "rb") as fh:
                resp = httpx.post(
                    url, data=data, files={"file": (audio.name, fh, "audio/wav")}, timeout=self.timeout
                )
        except httpx.HTTPError as exc:
            raise SttUnavailable(f"STT companion at {self.base_url} unreachable: {exc}") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"STT server error {resp.status_code}: {resp.text[:300]}")
        body = resp.json()
        on_progress(1.0)
        return parse_openai_verbose(body)


def parse_openai_verbose(body: dict[str, Any]) -> TranscriptionResult:
    words_all = [Word(float(w["start"]), float(w["end"]), w.get("word", "")) for w in body.get("words") or []]
    segs: list[Segment] = []
    for s in body.get("segments") or []:
        text = _clean(s.get("text", ""))
        if not text:
            continue
        st, en = float(s["start"]), float(s["end"])
        ws = [Word(float(w["start"]), float(w["end"]), w.get("word", "")) for w in s.get("words") or []] or [
            w for w in words_all if st <= (w.start + w.end) / 2 < en
        ]
        segs.append(Segment(st, en, text, ws))
    if not segs and body.get("text"):
        segs.append(Segment(0.0, float(body.get("duration") or 0.0), _clean(body["text"])))
    return TranscriptionResult(segs, body.get("language"), float(body.get("duration") or 0.0))


def result_to_openai(result: TranscriptionResult) -> dict[str, Any]:
    return {
        "task": "transcribe",
        "language": result.language,
        "duration": result.duration,
        "text": " ".join(s.text for s in result.segments),
        "segments": [
            {
                "id": i,
                "start": s.start,
                "end": s.end,
                "text": s.text,
                "words": [{"start": w.start, "end": w.end, "word": w.word} for w in s.words],
            }
            for i, s in enumerate(result.segments)
        ],
        "words": [{"start": w.start, "end": w.end, "word": w.word} for s in result.segments for w in s.words],
    }


def dumps(result: TranscriptionResult) -> str:
    return json.dumps(result_to_openai(result))
