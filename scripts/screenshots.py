"""Capture GUI screenshots with Playwright (app must be running on :8420)."""
import sys
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8420"
OUT = "docs/screenshots"
PAGES = sys.argv[2].split(",") if len(sys.argv) > 2 else ["dashboard:/", "courses:/courses", "login:/login", "models:/models", "queue:/queue"]

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    errors = []
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    for spec in PAGES:
        name, path = spec.split(":", 1)
        pg.goto(BASE + path)
        pg.wait_for_load_state("domcontentloaded")
        pg.wait_for_timeout(3500)
        pg.screenshot(path=f"{OUT}/{name}.png", full_page=False)
        print("saved", name)
    print("console errors:", errors)
    b.close()
