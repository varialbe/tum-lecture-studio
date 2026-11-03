# Repository & platform analysis (2026-11-03)

## Valentin-Metz/tum_video_scraper (last commit 2026-10-19)

* Python CLI, YAML config listing subjects as `year/term/slug` + camera type; no GUI, no persistent state.
* **Auth:** Selenium + Firefox (geckodriver). Clicks the first button on `/login`, types username/password
  into the IdP form, waits for a URL change. Fragile to every UI change (4 login-related fixes in Aug–Sep 2026).
* **Discovery:** loads `/?year=…&term=…&slug=…&view=3`, scrapes `<a href="/w/…">` links, then opens every
  watch page and pulls the `.m3u8` from the video.js player / Performance API (fix for the SPA, Oct 2026).
  No course catalog – course slugs must be known in advance.
* **Download:** `ffmpeg -i <m3u8> -c copy -f mp4` per video in a `multiprocessing.Process`, a semaphore for
  concurrency, `.lock` files for de-duplication, optional `auto-editor` jump-cut. No progress, no resume, no
  retries, no integrity check; downloads regardless of the course's *downloads enabled* flag.
* **Variants:** COMB / PRES / CAM via the watch-page suffix.
* **Docker:** `python:latest` + firefox-esr + geckodriver; config mounted at `/app/config.yml`.
* **Tests:** a handful of live Selenium tests (need real credentials).
* **License:** none in the repository → code is *not* copied; this project is an independent implementation
  that only reuses publicly observable protocol facts.

## TUM-Dev/gocast (TUM-Live server, MIT)

The server is now a Vue SPA backed by a documented **API v2** (grpc-gateway, `apiv2/server/apiv2.proto`,
served under `/api/v2`). Relevant endpoints, all verified against `live.rbg.tum.de` (v1.15.1):

| Endpoint | Use |
|---|---|
| `GET /semesters` | semester list + current semester (public) |
| `GET /login-options` | `{useSaml: true, idpName: "TUM Login"}` |
| `GET /courses?year&term` | public courses |
| `GET /courses/enrolled?year&term` | the user's courses (session) |
| `GET /courses/pinned` | pinned courses (session) |
| `GET /search?query&semesters=2026W` | courses visible to the caller (incl. "logged-in" courses) |
| `GET /courses/{slug}?year&term` | course incl. `streams[]` with `playlistUrl{,Pres,Cam}`, `duration`, and – only when the lecturer enabled downloads – `downloads[]` with signed `…&download=1` URLs |
| `GET /users/me` | session check |

* **Session:** HttpOnly cookie `jwt` (7-day TTL, `tools/session.go`); `/api/v2/auth/token` mints 15-minute
  bearer tokens from it.
* **Login:** SAML via `GET /saml/out` → `login.tum.de` (Shibboleth IdP: local-storage step, then
  `j_username`/`j_password`/`csrf_token`/`_eventId_proceed`, optional passkey) → auto-POST of `SAMLResponse`
  to `/shib` → `jwt` cookie. An internal form (`POST /login`, LDAP-backed when enabled) also exists.
* **Downloads:** the signed `download=1` URL on `edge.live.rbg.tum.de` serves a **faststart MP4** with
  `Accept-Ranges: bytes` and a real `Content-Length` (≈1.8 GB for 105 min COMB). The edge prepares the file
  first: 45–175 s until the first byte in our tests. Signed URLs expire (JWT `exp` ≈ 20 h).

## Consequences for this design

* No Selenium. Discovery and metadata come from API v2 – robust and fast.
* Downloads use the official download URLs only (= the lecturer's permission), with HTTP Range for real byte
  progress and genuine resume; ffmpeg HLS `-c copy` remains as a fallback engine.
* Signed URLs are re-fetched right before each attempt (they expire), which also detects expired sessions.
* Login is replayed with a plain HTTP client; any unknown IdP page (MFA, consent, notices) is handed to the
  user. A pasted `jwt` cookie works as a universal fallback (passkeys, unusual IdP flows).

## Speech-to-text selection

Candidates reviewed: Whisper large-v3, large-v3-turbo, faster-whisper/CTranslate2, whisper.cpp, mlx-whisper,
vllm-metal STT, Qwen3-ASR (open weights since 2026-01, 0.6B/1.7B, timestamps via a separate
Qwen3-ForcedAligner model), NVIDIA Parakeet-TDT-0.6B-v3 / Canary-1B-v2.

* Published German WERs of turbo, parakeet-v3 and canary-v2 are within ~0.1 pp of each other on
  FLEURS/MLS/CV (vendor tables), so engine maturity, timestamps, prompting and platform support decide.
* **faster-whisper** (MIT): CUDA + CPU, int8/fp16, built-in Silero VAD, word timestamps, `initial_prompt`
  and `hotwords` for vocabulary → default on NVIDIA/CPU.
* **mlx-whisper** (MIT): Metal on Apple Silicon, same Whisper checkpoints, word timestamps, prompt → default
  on macOS, run natively (Docker on macOS has no Metal passthrough).
* **vllm-metal STT** (Whisper + Qwen3-ASR, OpenAI-compatible `/v1/audio/transcriptions`) and Qwen3-ASR via
  vLLM are supported through the generic *remote* adapter instead of being bundled: heavier installs, and
  Qwen3-ASR needs the separate aligner for timestamps.
* Parakeet/Canary need NeMo (large dependency); no prompt/vocabulary support → not bundled.

Measured results on the available hardware are in [benchmarks.md](benchmarks.md).
