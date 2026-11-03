"""Application configuration.

Environment variables provide defaults (see .env.example); a few runtime
preferences are editable in the GUI and stored in the database (`settings`
table, see `db.Database.get_setting`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    data_dir: Path
    recordings_dir: Path
    transcripts_dir: Path
    models_dir: Path
    host: str = "127.0.0.1"
    port: int = 8420
    tum_base_url: str = "https://live.rbg.tum.de"
    download_concurrency: int = 2
    transcribe_concurrency: int = 1
    # URL of a host-native STT companion (e.g. MLX on macOS) used from Docker.
    stt_remote_url: str | None = None
    stt_default_model: str | None = None
    auto_transcribe: bool = False
    # Record HLS streams of lectures whose lecturer disabled downloads (opt-in).
    stream_capture: bool = False
    static_dir: Path | None = None
    # How long to wait for the edge server to start sending a prepared MP4.
    download_first_byte_timeout: float = 600.0
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "studio.db"

    @property
    def session_path(self) -> Path:
        return self.data_dir / "session.json"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.recordings_dir, self.transcripts_dir, self.models_dir, self.tmp_dir):
            p.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("TLS_DATA_DIR", "./data")).resolve()
    static = os.environ.get("TLS_STATIC_DIR")
    default_static = Path(__file__).parent / "static"
    return Settings(
        data_dir=data_dir,
        recordings_dir=Path(os.environ.get("TLS_RECORDINGS_DIR", data_dir / "recordings")).resolve(),
        transcripts_dir=Path(os.environ.get("TLS_TRANSCRIPTS_DIR", data_dir / "transcripts")).resolve(),
        models_dir=Path(os.environ.get("TLS_MODELS_DIR", data_dir / "models")).resolve(),
        host=os.environ.get("TLS_HOST", "127.0.0.1"),
        port=_env_int("TLS_PORT", 8420),
        tum_base_url=os.environ.get("TLS_TUM_BASE_URL", "https://live.rbg.tum.de").rstrip("/"),
        download_concurrency=max(1, _env_int("TLS_DOWNLOAD_CONCURRENCY", 2)),
        transcribe_concurrency=max(1, _env_int("TLS_TRANSCRIBE_CONCURRENCY", 1)),
        stt_remote_url=os.environ.get("TLS_STT_REMOTE_URL") or None,
        stt_default_model=os.environ.get("TLS_STT_MODEL") or None,
        auto_transcribe=_env_bool("TLS_AUTO_TRANSCRIBE", False),
        stream_capture=_env_bool("TLS_STREAM_CAPTURE", False),
        static_dir=Path(static) if static else (default_static if default_static.exists() else None),
    )
