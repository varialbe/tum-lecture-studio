"""Detect the inference capabilities of the current machine."""

from __future__ import annotations

import functools
import importlib.util
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Hardware:
    os: str
    arch: str
    cpu: str
    cpu_cores: int
    ram_gb: float
    in_docker: bool
    apple_silicon: bool
    cuda_devices: list[dict] = field(default_factory=list)  # name, vram_gb
    cuda_usable: bool = False  # CTranslate2 can actually use CUDA
    engines: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _has(mod: str) -> bool:
    return importlib.util.find_spec(mod) is not None


def _nvidia_smi() -> list[dict]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    devices = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 2 and parts[1].isdigit():
            devices.append({"name": parts[0], "vram_gb": round(int(parts[1]) / 1024, 1)})
    return devices


def register_cuda_dlls() -> None:
    """On Windows, make pip-installed CUDA libs (nvidia-cublas/cudnn) loadable by CTranslate2."""
    if sys.platform != "win32":
        return
    for base in map(Path, sys.path):
        nv = base / "nvidia"
        if not nv.is_dir():
            continue
        for bin_dir in nv.glob("*/bin"):
            try:
                os.add_dll_directory(str(bin_dir))
            except (OSError, AttributeError):
                pass
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"


@functools.lru_cache(maxsize=1)
def detect() -> Hardware:
    import psutil

    system = platform.system()
    arch = platform.machine().lower()
    cpu = platform.processor() or arch
    if system == "Darwin":
        try:
            cpu = (
                subprocess.run(
                    ["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, timeout=5
                ).stdout.strip()
                or cpu
            )
        except (OSError, subprocess.SubprocessError):
            pass
    elif sys.platform == "win32":  # (not `system`) so mypy skips this branch on Linux/macOS
        try:
            import winreg

            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            cpu = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip() or cpu
        except OSError:
            pass
    elif system == "Linux":
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    cpu = line.split(":", 1)[1].strip()
                    break
        except OSError:
            pass
    hw = Hardware(
        os=system,
        arch=arch,
        cpu=cpu,
        cpu_cores=psutil.cpu_count(logical=False) or os.cpu_count() or 1,
        ram_gb=round(psutil.virtual_memory().total / 1024**3, 1),
        in_docker=Path("/.dockerenv").exists() or os.environ.get("TLS_IN_DOCKER") == "1",
        apple_silicon=system == "Darwin" and arch in ("arm64", "aarch64"),
        cuda_devices=_nvidia_smi(),
    )
    hw.engines = {
        "faster-whisper": _has("faster_whisper"),
        "mlx-whisper": hw.apple_silicon and _has("mlx_whisper"),
        "remote": True,
    }
    if hw.engines["faster-whisper"]:
        register_cuda_dlls()
        try:
            import ctranslate2

            hw.cuda_usable = ctranslate2.get_cuda_device_count() > 0
        except Exception:  # noqa: BLE001
            hw.cuda_usable = False
    return hw
