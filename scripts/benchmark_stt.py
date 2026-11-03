"""Benchmark STT configurations on a clip of a real recording.

Measures wall-clock time, real-time factor and peak memory (GPU memory via
nvidia-smi for CUDA runs, process RSS otherwise) and writes each transcript.
If --reference is given, WER/CER are computed against it; otherwise each run is
compared with the first config ("disagreement", NOT accuracy).

Each config runs in a fresh subprocess so memory numbers don't leak between runs.

Usage:
  python scripts/benchmark_stt.py MEDIA --start 600 --seconds 180 \
      --config fw-large-v3:cuda:int8_float16 --config fw-large-v3-turbo:cuda:float16 ...
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))


def run_one(args: argparse.Namespace) -> None:
    """Child mode: transcribe once and print a JSON result line."""
    from lecture_studio.stt import catalog
    from lecture_studio.stt.base import DEFAULT_VOCABULARY, TranscribeOptions, build_prompt
    from lecture_studio.stt.engines import FasterWhisperEngine, MlxWhisperEngine
    from lecture_studio.stt.hardware import detect
    from lecture_studio.stt.memory import MemorySampler

    model_id, device, ct = args.child.split(":")
    spec = catalog.get_spec(model_id)
    store = catalog.ModelStore(Path(args.models_dir))
    if not store.installed(spec):
        store.download(spec)
    path = store.local_path(spec)
    hw = detect()
    t_load = time.time()
    if spec.engine == "mlx-whisper":
        engine = MlxWhisperEngine(path, model_id)
    else:
        engine = FasterWhisperEngine(path, model_id, device=device, compute_type=ct)
        engine._load()
    load_s = time.time() - t_load
    opts = TranscribeOptions(language=args.language or None,
                             initial_prompt=None if args.no_prompt else build_prompt(DEFAULT_VOCABULARY))
    sampler = MemorySampler(cuda=device == "cuda", interval=0.25)
    sampler.start()
    t0 = time.time()
    res = engine.transcribe(Path(args.wav), opts, lambda _f: None)
    wall = time.time() - t0
    peak = sampler.stop()
    text = " ".join(s.text for s in res.segments)
    dev_label = (hw.cuda_devices[0]["name"] if device == "cuda" and hw.cuda_devices else
                 f"{hw.cpu} ({hw.cpu_cores} cores)" if device == "cpu" else device)
    print("RESULT " + json.dumps({"config": args.child, "device": dev_label, "load_s": load_s, "wall_s": wall,
                                  "peak_mb": peak, "text": text, "segments": len(res.segments),
                                  "language": res.language}, ensure_ascii=False))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("media", nargs="?")
    ap.add_argument("--start", type=float, default=600)
    ap.add_argument("--seconds", type=float, default=180)
    ap.add_argument("--config", action="append", default=[])
    ap.add_argument("--reference", help="text file with a human reference transcript")
    ap.add_argument("--language", default="de")
    ap.add_argument("--no-prompt", action="store_true")
    ap.add_argument("--models-dir", default=str(ROOT / "data" / "models"))
    ap.add_argument("--out", default=str(ROOT / "data" / "benchmarks"))
    ap.add_argument("--child")
    ap.add_argument("--wav")
    args = ap.parse_args()
    if args.child:
        return run_one(args)

    from lecture_studio.stt.audio import extract_audio
    from lecture_studio.stt.wer import cer, wer

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    wav = out / f"clip_{int(args.start)}_{int(args.seconds)}.wav"
    asyncio.run(extract_audio(Path(args.media), wav, None, lambda _f: None, start=args.start, seconds=args.seconds))
    ref_text = Path(args.reference).read_text(encoding="utf-8") if args.reference else None
    results = []
    for cfg in args.config:
        print(f"== {cfg}", flush=True)
        proc = subprocess.run([sys.executable, __file__, "--child", cfg, "--wav", str(wav), "--language",
                               args.language, "--models-dir", args.models_dir] +
                              (["--no-prompt"] if args.no_prompt else []),
                              capture_output=True, text=True, encoding="utf-8")
        line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")), None)
        if not line:
            print(proc.stderr[-1500:])
            results.append({"config": cfg, "error": proc.stderr.strip().splitlines()[-1:]})
            continue
        r = json.loads(line[7:])
        r["rtf"] = r["wall_s"] / args.seconds
        (out / f"{cfg.replace(':', '_')}.txt").write_text(r["text"], encoding="utf-8")
        results.append(r)
        print(f"   wall {r['wall_s']:.1f}s  RTF {r['rtf']:.3f}  peak {r['peak_mb']:.0f} MB  load {r['load_s']:.1f}s")
    base = ref_text or next((r["text"] for r in results if "text" in r), "")
    for r in results:
        if "text" in r:
            r["wer"] = wer(base, r["text"])
            r["cer"] = cer(base, r["text"])
    summary = {"media": str(args.media), "clip_start": args.start, "clip_seconds": args.seconds,
               "reference": "human" if ref_text else f"first config ({args.config[0]})", "results": results}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n| config | device | wall s | RTF | peak MB | WER* | CER* |")
    for r in results:
        if "text" in r:
            print(f"| {r['config']} | {r['device']} | {r['wall_s']:.1f} | {r['rtf']:.3f} | {r['peak_mb']:.0f} | "
                  f"{r['wer']:.3f} | {r['cer']:.3f} |")
        else:
            print(f"| {r['config']} | failed: {r['error']} |")


if __name__ == "__main__":
    main()
