# Verification status (2026-11-03)

Test machine: Windows 10 Pro, AMD Ryzen 7 5700X (8 cores), 48 GB RAM, NVIDIA RTX 2070 Super 8 GB
(driver 595.97), Python 3.11.9, ffmpeg 9.0.2, Node 20.10, faster-whisper 1.2.1 / CTranslate2 4.8.5.

## Verified

| Area | How | Result |
|---|---|---|
| Unit tests | `pytest` (backend) | **47/47 pass**: course parsing, recording metadata, auth flows (mocked IdP: success, wrong password, MFA step, foreign-redirect refusal), session store, download lifecycle, Range resume, duplicate detection, cancel, restart recovery, corrupt-file rejection, filename sanitising, chunk planning and merging, exports, API security guards, search |
| Lint / types | `ruff check`, `ruff format --check`, `mypy`, `tsc` | clean |
| Course discovery (live) | `GET /api/courses?semester=2026W` against live.rbg.tum.de, anonymous | 5 public courses with recording counts and download permission |
| GUI end-to-end (live) | `scripts/e2e_gui.py` (Playwright, headless Chromium) | **pass**: course explorer → *Vorkurs Höhere Mathematik* → *Vorkurs: Trigonometrie* → Download → Transcribe → transcript view → SRT export → in-lecture search; no console errors |
| Real download | same run, COMB variant | 1,792,379,621 bytes (= server Content-Length), 115 min, verified with ffprobe |
| Resume after crash | server killed at 82 % (1,485,832,192 bytes), restarted | job re-queued automatically and continued with `Range: bytes=1485832192-`, no restart from zero |
| Real progress | job events during the run | byte progress and speed while downloading; indeterminate bar during the edge server's 36 s preparation wait |
| Transcription (GPU) | whole lecture, fw-large-v3-turbo fp16, CUDA, VAD, German | 114.9 min of audio in 131 s (RTF 0.019), 1276 segments; chunk boundaries checked by hand: no lost or repeated words |
| Exports | GUI download (SRT) + files on disk | TXT, MD, SRT, VTT, JSON written; SRT parsed |
| Host STT companion | `lecture-studio-stt` (faster-whisper, CUDA) + `RemoteEngine` | OpenAI-compatible request returned segments with word timestamps (45 s clip in 2.7 s) |
| Persistence | restart during download (above) | jobs, library and transcripts survive restarts |

## Not verified, and why

| Area | Status |
|---|---|
| **TUM SSO login with real credentials** | Not run: no credentials were available to the agent. The flow is implemented against the real login.tum.de form structure (fields inspected live on 2026-11-03) and tested with a mocked IdP. Needs one manual sign-in to confirm. |
| Enrolled/"logged-in" courses and the three priority courses | Needs a signed-in session. The matching logic is unit-tested; whether TUM-Live lists the courses under these names this semester is unverified. |
| Docker images | `Dockerfile` and `docker-compose.yml` are written but **never built or run**: Docker isn't installed on the test machine, and a build on another server wasn't permitted. CI builds both targets (amd64 + arm64 CPU, amd64 CUDA) once the repo is on GitHub, which proves only that they build. |
| macOS / Apple Silicon / MLX | Not run (no Mac available). The MLX engine adapter and the companion architecture are implemented; the companion was verified only with faster-whisper. |
| Linux native | Not run. |
| NVIDIA in Docker (Container Toolkit) | Not run. |
| ffmpeg HLS fallback engine | Implemented; not exercised live, because every tested lecture offered a direct MP4. |
| Interactive MFA on the real IdP | Mocked only. |
