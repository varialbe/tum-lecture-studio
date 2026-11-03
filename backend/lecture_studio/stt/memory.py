"""Sample peak memory during inference.

GPU: per-process usage from nvidia-smi when the driver reports it (Linux, TCC);
under Windows WDDM it reports "[N/A]", so we fall back to the device-wide memory
increase over a baseline taken when the sampler is created (create it *before*
loading the model). Other GPU users can add noise to that fallback.
CPU: peak resident set size of this process.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading

import psutil


def _smi(*args: str) -> str:
    try:
        return subprocess.run(["nvidia-smi", *args], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def device_used_mb() -> float:
    out = _smi("--query-gpu=memory.used", "--format=csv,noheader,nounits")
    try:
        return max(float(x) for x in out.split()) if out.strip() else 0.0
    except ValueError:
        return 0.0


class MemorySampler:
    def __init__(self, cuda: bool = False, interval: float = 1.0):
        self.cuda = cuda and shutil.which("nvidia-smi") is not None
        self.interval = interval
        self.peak_rss = 0
        self.peak_proc_gpu = 0.0
        self.peak_device = 0.0
        self.baseline = device_used_mb() if self.cuda else 0.0
        self.method = "rss"
        self._stop = threading.Event()
        self._t: threading.Thread | None = None
        self._proc = psutil.Process(os.getpid())

    def _proc_gpu_mb(self) -> float:
        total = 0.0
        for line in _smi("--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits").splitlines():
            parts = [x.strip() for x in line.split(",")]
            if len(parts) == 2 and parts[0].isdigit() and int(parts[0]) == os.getpid():
                try:
                    total += float(parts[1])
                except ValueError:  # "[N/A]" under WDDM
                    pass
        return total

    def sample(self) -> None:
        try:
            self.peak_rss = max(self.peak_rss, self._proc.memory_info().rss)
        except psutil.Error:
            pass
        if self.cuda:
            self.peak_proc_gpu = max(self.peak_proc_gpu, self._proc_gpu_mb())
            self.peak_device = max(self.peak_device, device_used_mb())

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            self.sample()

    def start(self) -> None:
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def stop(self) -> float:
        """Peak in MB: GPU memory for CUDA runs (per-process or device delta), else process RSS."""
        self._stop.set()
        if self._t:
            self._t.join(timeout=5)
        self.sample()
        if self.cuda and self.peak_proc_gpu:
            self.method = "gpu-process"
            return self.peak_proc_gpu
        if self.cuda and self.peak_device > self.baseline:
            self.method = "gpu-device-delta"
            return self.peak_device - self.baseline
        self.method = "rss"
        return self.peak_rss / 1024**2
