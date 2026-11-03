"""Model catalog and on-disk model management (Hugging Face cache layout).

Sizes are approximate weight download sizes; memory figures are rough
working-set estimates from the model's parameter count and precision, shown
as "approx." in the GUI. Speed is never estimated here - it comes from real
benchmarks stored in the database.
"""

from __future__ import annotations

import os
import shutil
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from .hardware import Hardware


@dataclass
class ModelSpec:
    id: str
    engine: str  # faster-whisper | mlx-whisper | remote
    repo: str
    label: str
    params: str
    download_gb: float
    memory_gb: float  # approx. peak RAM/VRAM in the default precision
    platforms: list[str]  # cuda | cpu | apple
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


CATALOG: list[ModelSpec] = [
    ModelSpec(
        "fw-large-v3-turbo",
        "faster-whisper",
        "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        "Whisper large-v3-turbo (CTranslate2)",
        "809M",
        1.6,
        2.5,
        ["cuda", "cpu"],
        "Best speed/accuracy balance. Pruned decoder (4 layers); German WER close to large-v3.",
    ),
    ModelSpec(
        "fw-large-v3",
        "faster-whisper",
        "Systran/faster-whisper-large-v3",
        "Whisper large-v3 (CTranslate2)",
        "1.55B",
        3.1,
        4.5,
        ["cuda", "cpu"],
        "Highest Whisper accuracy, ~2-4x slower than turbo. int8_float16 fits easily in 8 GB VRAM.",
    ),
    ModelSpec(
        "fw-medium",
        "faster-whisper",
        "Systran/faster-whisper-medium",
        "Whisper medium (CTranslate2)",
        "769M",
        1.5,
        2.0,
        ["cuda", "cpu"],
        "Older full-decoder model; usually worse than turbo at similar cost.",
    ),
    ModelSpec(
        "fw-small",
        "faster-whisper",
        "Systran/faster-whisper-small",
        "Whisper small (CTranslate2)",
        "244M",
        0.5,
        1.0,
        ["cuda", "cpu"],
        "CPU fallback for weak machines; noticeably more errors on technical German.",
    ),
    ModelSpec(
        "mlx-large-v3-turbo",
        "mlx-whisper",
        "mlx-community/whisper-large-v3-turbo",
        "Whisper large-v3-turbo (MLX, Metal)",
        "809M",
        1.6,
        2.5,
        ["apple"],
        "Native Apple Silicon GPU. Run via the host STT companion when the app is in Docker.",
    ),
    ModelSpec(
        "mlx-large-v3",
        "mlx-whisper",
        "mlx-community/whisper-large-v3-mlx",
        "Whisper large-v3 (MLX, Metal)",
        "1.55B",
        3.1,
        4.5,
        ["apple"],
        "Most accurate Whisper on Apple Silicon; slower than turbo.",
    ),
]

REMOTE_MODEL = ModelSpec(
    "remote",
    "remote",
    "",
    "Host STT companion / OpenAI-compatible server",
    "-",
    0,
    0,
    ["cuda", "cpu", "apple"],
    "Uses TLS_STT_REMOTE_URL (e.g. the native MLX companion on macOS, or vllm-metal).",
)


def get_spec(model_id: str) -> ModelSpec:
    if model_id == "remote":
        return REMOTE_MODEL
    for m in CATALOG:
        if m.id == model_id:
            return m
    raise KeyError(model_id)


def compatible(spec: ModelSpec, hw: Hardware) -> bool:
    if spec.engine == "remote":
        return True
    if spec.engine == "mlx-whisper":
        return hw.apple_silicon and not hw.in_docker
    return True  # faster-whisper runs on CPU everywhere


def recommend(hw: Hardware, remote_configured: bool) -> tuple[str, str, str]:
    """Returns (model_id, compute_type, reason)."""
    if hw.apple_silicon and hw.engines.get("mlx-whisper"):
        return "mlx-large-v3-turbo", "float16", "Apple Silicon: MLX uses the Metal GPU natively."
    if remote_configured:
        return "remote", "default", "A host STT companion is configured (e.g. native MLX on macOS)."
    if hw.cuda_usable:
        vram = max((d["vram_gb"] for d in hw.cuda_devices), default=0)
        if vram >= 4:
            return "fw-large-v3-turbo", "float16", f"NVIDIA GPU with {vram:.0f} GB VRAM: turbo in fp16 fits."
        return "fw-large-v3-turbo", "int8_float16", "Small GPU: int8 weights keep VRAM low."
    if hw.apple_silicon:
        return (
            "fw-large-v3-turbo",
            "int8",
            (
                "Apple Silicon without MLX (e.g. inside Docker): CPU only. "
                "Install the native companion for Metal acceleration."
            ),
        )
    return "fw-large-v3-turbo", "int8", "No supported GPU detected: CPU int8 (slow but accurate)."


def compute_types(spec: ModelSpec, hw: Hardware) -> list[str]:
    if spec.engine == "faster-whisper":
        return ["float16", "int8_float16", "int8"] if hw.cuda_usable else ["int8", "float32"]
    if spec.engine == "mlx-whisper":
        return ["float16"]
    return ["default"]


class ModelStore:
    """Wraps the Hugging Face hub cache in `models_dir`."""

    def __init__(self, models_dir: Path):
        self.dir = models_dir
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        self._downloads: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _repo_dir(self, repo: str) -> Path:
        return self.dir / ("models--" + repo.replace("/", "--"))

    def installed(self, spec: ModelSpec) -> bool:
        if spec.engine == "remote":
            return True
        snaps = self._repo_dir(spec.repo) / "snapshots"
        if not snaps.is_dir():
            return False
        for snap in snaps.iterdir():
            names = {p.name for p in snap.iterdir()}
            if spec.engine == "faster-whisper" and "model.bin" in names:
                return True
            if spec.engine == "mlx-whisper" and ({"weights.npz", "weights.safetensors"} & names):
                return True
        return False

    def size_on_disk(self, spec: ModelSpec) -> int:
        d = self._repo_dir(spec.repo) / "blobs"
        if not d.is_dir():
            return 0
        return sum(p.stat().st_size for p in d.iterdir() if p.is_file())

    def local_path(self, spec: ModelSpec) -> str | None:
        snaps = self._repo_dir(spec.repo) / "snapshots"
        if not snaps.is_dir():
            return None
        for snap in sorted(snaps.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            return str(snap)
        return None

    def download_state(self, model_id: str) -> dict | None:
        return self._downloads.get(model_id)

    def download(self, spec: ModelSpec) -> None:
        """Blocking download with real byte progress (polls the blob dir)."""
        from huggingface_hub import HfApi, snapshot_download

        with self._lock:
            if self._downloads.get(spec.id, {}).get("status") == "running":
                return
            self._downloads[spec.id] = {"status": "running", "bytes_done": 0, "bytes_total": None}
        state = self._downloads[spec.id]
        try:
            info = HfApi().model_info(spec.repo, files_metadata=True)
            state["bytes_total"] = sum((s.size or 0) for s in info.siblings or [])
            stop = threading.Event()

            def poll() -> None:
                blobs = self._repo_dir(spec.repo) / "blobs"
                while not stop.wait(0.5):
                    if blobs.is_dir():
                        state["bytes_done"] = sum(p.stat().st_size for p in blobs.iterdir() if p.is_file())

            t = threading.Thread(target=poll, daemon=True)
            t.start()
            try:
                snapshot_download(spec.repo, cache_dir=str(self.dir))
            finally:
                stop.set()
                t.join()
            state.update(status="done", bytes_done=state["bytes_total"])
        except Exception as exc:  # noqa: BLE001
            state.update(status="error", error=str(exc)[:300])
            raise

    def remove(self, spec: ModelSpec) -> None:
        shutil.rmtree(self._repo_dir(spec.repo), ignore_errors=True)
        self._downloads.pop(spec.id, None)
