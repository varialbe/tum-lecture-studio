"""Sample peak memory during inference (process RSS, plus GPU memory via nvidia-smi)."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading

import psutil


class MemorySampler:
    def __init__(self, cuda: bool = False, interval: float = 1.0):
        self.cuda = cuda and shutil.which("nvidia-smi") is not None
        self.interval = interval
        self.peak_rss = 0
        self.peak_gpu_mb = 0.0
        self._stop = threading.Event()
        self._t: threading.Thread | None = None
        self._proc = psutil.Process(os.getpid())

    def _gpu_used_mb(self) -> float:
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return 0.0
        total = 0.0
        for line in out.splitlines():
            parts = [x.strip() for x in line.split(",")]
            if len(parts) == 2 and parts[0].isdigit() and int(parts[0]) == os.getpid():
                try:
                    total += float(parts[1])
                except ValueError:
                    pass
        return total

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.peak_rss = max(self.peak_rss, self._proc.memory_info().rss)
            except psutil.Error:
                pass
            if self.cuda:
                self.peak_gpu_mb = max(self.peak_gpu_mb, self._gpu_used_mb())

    def start(self) -> None:
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def stop(self) -> float:
        """Returns the peak in MB: GPU memory when sampling CUDA, else process RSS."""
        self._stop.set()
        if self._t:
            self._t.join(timeout=5)
        if self.cuda and self.peak_gpu_mb:
            return self.peak_gpu_mb
        return self.peak_rss / 1024**2
