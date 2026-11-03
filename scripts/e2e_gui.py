"""End-to-end GUI test against the running app and the real TUM-Live service.

Drives the browser like a user: open the course explorer, open a course, download a
lecture, transcribe it, open the transcript and export SRT. Uses a *public* course
with lecturer-enabled downloads, so it needs no credentials.

Usage: python scripts/e2e_gui.py [--course "Vorkurs Höhere Mathematik"] [--lecture "Trigonometrie"]
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://127.0.0.1:8420")
ap.add_argument("--course", default="Vorkurs Höhere Mathematik")
ap.add_argument("--lecture", default="Trigonometrie")
ap.add_argument("--out", default="docs/screenshots")
ap.add_argument("--timeout-min", type=float, default=60)
args = ap.parse_args()
OUT = Path(args.out)
OUT.mkdir(parents=True, exist_ok=True)


def jobs() -> list[dict]:
    for _ in range(60):  # tolerate a backend restart while waiting
        try:
            with urllib.request.urlopen(f"{args.base}/api/jobs") as r:
                return json.load(r)["jobs"]
        except OSError:
            time.sleep(2)
    raise SystemExit("backend unreachable")


def wait_job(kind: str, title_part: str, page, shot: str) -> dict:
    deadline = time.time() + args.timeout_min * 60
    took_progress_shot = False
    last = ""
    while time.time() < deadline:
        js = [j for j in jobs() if j["kind"] == kind and title_part in (j["title"] or "")]
        if js:
            j = js[0]
            line = f"{kind}: {j['status']} {j['stage']} {j['progress']} {j['message'] or ''}"
            if line != last:
                print(time.strftime("%H:%M:%S"), line[:160], flush=True)
                last = line
            if j["status"] in ("completed", "failed", "cancelled"):
                return j
            if not took_progress_shot and j["progress"] and j["progress"] > 0.15:
                page.goto(f"{args.base}/queue")
                page.wait_for_timeout(2000)
                page.screenshot(path=str(OUT / f"{shot}.png"))
                took_progress_shot = True
        time.sleep(3)
    raise SystemExit(f"timeout waiting for {kind}")


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, accept_downloads=True)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

    # 1-3: discover courses and open one
    page.goto(f"{args.base}/courses")
    card = page.get_by_role("link", name=re.compile(re.escape(args.course)))
    expect(card.first).to_be_visible(timeout=60_000)
    card.first.click()
    row = page.locator("tr", has_text=args.lecture).first
    expect(row).to_be_visible(timeout=60_000)
    page.screenshot(path=str(OUT / "recordings.png"))

    # 4-5: download (skipped if this lecture is already on disk or in the queue)
    btn = row.get_by_role("button", name=re.compile("^(Download|Retry)$"))
    if btn.count():
        btn.click()
        expect(page.get_by_text(re.compile("queued|already"))).to_be_visible(timeout=15_000)
    dl = wait_job("download", args.lecture, page, "queue-downloading")
    assert dl["status"] == "completed", dl
    path = Path(dl["output_path"])
    print("downloaded:", path, path.stat().st_size, "bytes")
    assert path.stat().st_size == dl["bytes_total"]

    # 6-8: transcribe from the GUI
    page.goto(f"{args.base}/courses")
    page.get_by_role("link", name=re.compile(re.escape(args.course))).first.click()
    row = page.locator("tr", has_text=args.lecture).first
    tbtn = row.get_by_role("button", name="Transcribe")
    if tbtn.count():
        tbtn.click()
    tx = wait_job("transcribe", args.lecture, page, "queue-transcribing")
    assert tx["status"] == "completed", tx
    print("transcription:", tx["message"])

    # 9: the GUI reflects completion; open transcript and export
    page.goto(f"{args.base}/courses")
    page.get_by_role("link", name=re.compile(re.escape(args.course))).first.click()
    row = page.locator("tr", has_text=args.lecture).first
    expect(row.get_by_role("link", name="Open")).to_be_visible(timeout=15_000)
    row.get_by_role("link", name="Open").click()
    expect(page.get_by_role("heading", level=1)).to_contain_text(args.lecture, timeout=15_000)
    page.wait_for_timeout(1500)
    page.screenshot(path=str(OUT / "transcript.png"))
    with page.expect_download() as d:
        page.get_by_role("link", name="SRT").click()
    srt = Path(d.value.path()).read_text(encoding="utf-8")
    assert re.search(r"^1\n00:\d\d:\d\d,\d{3} --> ", srt, re.M), srt[:200]
    print("SRT export ok:", len(srt.splitlines()), "lines")
    # search inside the transcript
    page.get_by_label("Search in transcript").fill("sinus")
    page.wait_for_timeout(500)
    print("in-page search:", page.locator("text=/\\d+ matches/").inner_text())
    print("console errors:", errors)
    browser.close()
print("E2E OK")
