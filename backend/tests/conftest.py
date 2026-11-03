from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from lecture_studio.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def course_json() -> dict:
    return json.loads((FIXTURES / "course_vorkurs.json").read_text(encoding="utf-8"))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings(
        data_dir=tmp_path / "data",
        recordings_dir=tmp_path / "data" / "recordings",
        transcripts_dir=tmp_path / "data" / "transcripts",
        models_dir=tmp_path / "data" / "models",
        download_first_byte_timeout=10,
    )
    s.ensure_dirs()
    return s


needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


@pytest.fixture(scope="session")
def sample_mp4(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A small real MP4 (video + audio) generated with ffmpeg."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("media") / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x240:d=8",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=8",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(out),
        ],
        check=True,
    )
    return out
