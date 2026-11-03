# Verification status (2026-11-03)

Test machine: Windows 10 Pro, AMD Ryzen 7 5700X (8 cores), 48 GB RAM, NVIDIA RTX 2070 Super 8 GB
(driver 595.97), Python 3.11.9, ffmpeg 9.0.2, Node 20.10, faster-whisper 1.2.1 / CTranslate2 4.8.5.

## Verified

| Area | How | Result |
|---|---|---|
| Unit tests | `pytest` (backend) | **52/52 pass**: course parsing, recording metadata, auth flows (mocked IdP: success, wrong password, MFA step, foreign-redirect refusal), session store, download lifecycle, Range resume, duplicate detection, cancel, restart recovery, corrupt-file rejection, filename sanitising, chunk planning and merging, subtitle-length splitting, WER/CER, exports, API security guards, search |
| Lint / types | `ruff check`, `ruff format --check`, `mypy`, `tsc` | clean |
| **TUM SSO login (live)** | `scripts/login_from_env.py` → `/api/session/login` with a real TUM account (no MFA prompt for this account) | **authenticated**; the saved session survived an app restart |
| Course discovery (live) | `GET /api/courses?semester=2026W`, anonymous and signed in | anonymous: 5 public courses; signed in: **69 courses**, including the enrolled ones |
| Priority courses | same, signed in | all three matched to real TUM-Live courses: *Diskrete Strukturen (IN0015)* (6 recordings), *Einführung in die Rechnerarchitektur (IN0004)* (9), *Einführung in die Informatik und das Programmieren (CIT123001)* (6). **Downloads are disabled by the lecturers for all three** this semester, so the app shows them as stream-only and won't download them |
| GUI end-to-end (live) | `scripts/e2e_gui.py` (Playwright, headless Chromium) | **pass**: course explorer → *Vorkurs Höhere Mathematik* → *Vorkurs: Trigonometrie* → Download → Transcribe → transcript view → SRT export → in-lecture search; no console errors |
| Real download | same run, COMB variant | 1,792,379,621 bytes (= server Content-Length), 115 min, verified with ffprobe |
| Resume after crash | server killed at 82 % (1,485,832,192 bytes), restarted | job re-queued automatically and continued with `Range: bytes=1485832192-`, no restart from zero |
| Real progress | job events during the run | byte progress and speed while downloading; indeterminate bar during the edge server's 36 s preparation wait |
| Transcription (GPU) | whole lecture, fw-large-v3-turbo fp16, CUDA, VAD, German | 114.9 min of audio in 131 s (RTF 0.019), 1276 segments; chunk boundaries checked by hand: no lost or repeated words |
| Exports | GUI download (SRT) + files on disk | TXT, MD, SRT, VTT, JSON written; SRT parsed |
| Host STT companion | `lecture-studio-stt` (faster-whisper, CUDA) + `RemoteEngine` | OpenAI-compatible request returned segments with word timestamps (45 s clip in 2.7 s) |
| Persistence | restart during download (above) | jobs, library and transcripts survive restarts |
| **Docker (Linux, CPU image)** | built `--target cpu` on an Ubuntu VPS (AMD EPYC, 6 vCPU, 11.7 GB, Docker 29.9), run with `docker compose up studio` | image builds; container healthy; GUI served; hardware detected (`in_docker=true`, recommends turbo int8); public discovery works; real download in the container (2,232,100,334 bytes, byte-exact, 241 s); 3-minute CPU transcription (RTF 0.84) with all five export files written to the host volume |
| Subtitle cue length | 3-minute clip after the fix | the Docker run showed a 54 s VTT cue (VAD-merged segment); long segments are now split at word timestamps, preferring sentence ends: max 7.9 s, mean 3.0 s |
| STT benchmark | `scripts/benchmark_stt.py`, 6 configurations | see [benchmarks.md](benchmarks.md) |

## Not verified, and why

| Area | Status |
|---|---|
| Downloading an authenticated-only lecture | No course visible to the test account offers downloads beyond the public ones, so the download path was verified only with public courses (the API calls are the same, plus the session cookie). |
| Interactive MFA / consent on the real IdP | The test account got no second factor, so only the mocked flow tests this. |
| Docker CUDA image, arm64 image | Not built locally (the VPS has no GPU and is amd64). CI builds them, which proves only that they build. |
| Docker on Windows/macOS | Not run (Docker isn't installed on the test PC; no Mac). |
| macOS / Apple Silicon / MLX | Not run (no Mac available). The MLX engine adapter and the companion architecture are implemented; the companion was verified only with faster-whisper. |
| Linux native (without Docker) | Not run; the Linux code path ran inside the container. |
| NVIDIA in Docker (Container Toolkit) | Not run. |
| ffmpeg HLS fallback engine | Implemented; not exercised live, because every tested lecture offered a direct MP4. |
