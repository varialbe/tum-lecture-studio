# Speech-to-text benchmarks

Measured with `scripts/benchmark_stt.py`; raw numbers are in
[benchmark-2070s-summary.json](benchmark-2070s-summary.json) (transcript text is kept out of the repository).

## Setup

* **Audio:** 3:00 min (25:00–28:00) of *Vorkurs: Trigonometrie*, from TUM-Live's public course
  "Vorkurs Höhere Mathematik (DE)", WiSe 2026/27. German lecture with continuous speech and spoken math
  (Sinus/Kosinus, Pi, Symmetrie, Nullstellen, Periodizität).
* **Machine:** Windows 10, RTX 2070 Super 8 GB (driver 595.97), Ryzen 7 5700X, 48 GB RAM.
* **Software:** faster-whisper 1.2.1, CTranslate2 4.8.5, beam size 5, Silero VAD on, `language="de"`,
  vocabulary prompt on (the app's defaults).
* Each configuration runs in a fresh process. Wall time excludes model loading (1.5–5 s).
* **Memory:** under Windows (WDDM), nvidia-smi doesn't report per-process VRAM, so the GPU figure is the
  device-wide increase over the baseline before the model loaded. CPU runs report peak process RSS.

## Results

| Model | Device | Precision | Wall (3 min audio) | RTF | × real time | Peak memory | WER* | CER* |
|---|---|---|---|---|---|---|---|---|
| large-v3 | RTX 2070 S | float16 | 12.9 s | 0.072 | 14× | 4.2 GB VRAM | – (reference) | – |
| large-v3 | RTX 2070 S | int8_float16 | 17.0 s | 0.094 | 11× | 2.4 GB VRAM | 0.3 % | 0.1 % |
| **large-v3-turbo** | RTX 2070 S | **float16** | **3.9 s** | **0.022** | **46×** | 2.3 GB VRAM | 4.4 % | 1.6 % |
| large-v3-turbo | RTX 2070 S | int8_float16 | 4.7 s | 0.026 | 38× | 1.2 GB VRAM | 5.2 % | 2.8 % |
| large-v3-turbo | Ryzen 7 5700X | int8 | 42.3 s | 0.235 | 4.3× | 1.9 GB RAM | 5.4 % | 2.2 % |
| small | Ryzen 7 5700X | int8 | 29.9 s | 0.166 | 6.0× | 0.9 GB RAM | 10.8 % | 6.2 % |

\* **No human reference transcript exists** for this lecture: TUM-Live has no subtitles for it. WER/CER are
therefore **disagreement with large-v3 fp16**, after lower-casing and stripping punctuation. They rank the
configurations but aren't absolute accuracy.

**Whole-lecture run** (in the app, through the GUI): 114.9 min of the same lecture with large-v3-turbo fp16
took **131 s** of transcription (RTF 0.019) plus 5 s of audio extraction. VAD skips the 11 minutes of silence
before the lecture.

## Qualitative check (reading the transcripts)

* **large-v3:** correct domain terms ("Wertebereich", "gegeneinander verschoben", "2 Pi periodisch",
  "ganzzahligen", "Nullstellen"). Errors: "Symmetris" (symmetrisch), and "spielen" where the lecturer said
  "spiegeln" (turbo makes the same mistake).
* **large-v3-turbo:** nearly identical wording. Most of its 4.4 % disagreement is sentence segmentation and
  spelling variants ("Kosinus" vs. "Cosinus"). Real errors: "2Pi" → "2P" twice, "dieselben" →
  "die seltenen", "gegen einer" for "gegeneinander". It also caught "symmetrisch" where large-v3 wrote
  "Symmetris".
* **small:** noticeably worse: "Wertepereich", "muss zuerst" for "Muster ist", dropped words ("ihn zwar",
  "an der y-Achse"), and "zwei Pi Ja, aber aus der Definition" for "2 Pi periodisch".
* Spoken math stays as words ("Sinus x zum Quadrat", "minus 1 bis 1"). No configuration invents formula
  notation, and the app doesn't add any.

## Recommendations (encoded in `stt/catalog.py`)

* **NVIDIA (8 GB):** large-v3-turbo fp16 is the default: about 46× real time, so a 90-minute lecture takes
  about 2 minutes, in 2.3 GB of VRAM. large-v3 fp16 also fits easily in 8 GB (4.2 GB) and is worth choosing
  when exact wording matters more than speed (3.5× slower). An RTX 2070 Super handles turbo comfortably;
  int8 helps only on smaller GPUs.
* **CPU only:** large-v3-turbo int8 still runs 4× faster than real time on an 8-core desktop CPU. Prefer it
  over small, which is only ~40 % faster and clearly less accurate.
* **Apple Silicon:** mlx-whisper large-v3-turbo (Metal). **Not measured:** no Mac was available. Run
  `scripts/benchmark_stt.py` with `--config mlx-large-v3-turbo:metal:float16` on the M5 to fill in this row.
