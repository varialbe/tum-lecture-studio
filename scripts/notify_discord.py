#!/usr/bin/env python3
"""Send a development progress update to Discord.

The webhook URL is read from DISCORD_WEBHOOK_URL (environment or the repo's
.env file). Failures are reported on stderr but never raise, so a broken
webhook can never interrupt development or CI.

Usage: python scripts/notify_discord.py "message text"   (or pipe via stdin)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path


def _webhook_url() -> str | None:
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if url:
        return url
    env = Path(__file__).resolve().parent.parent / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("DISCORD_WEBHOOK_URL="):
                return line.split("=", 1)[1].strip().strip('"') or None
    return None


def send(message: str) -> bool:
    url = _webhook_url()
    if not url or "PASTE" in url:
        print("notify_discord: no webhook configured, skipping", file=sys.stderr)
        return False
    body = json.dumps({"content": message[:1990]}).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json", "User-Agent": "tum-lecture-studio-dev"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except Exception as exc:  # noqa: BLE001 - must never break the caller
        print(f"notify_discord: failed ({type(exc).__name__})", file=sys.stderr)
        return False


if __name__ == "__main__":
    text = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else sys.stdin.read()
    ok = send(text.strip())
    sys.exit(0 if ok or True else 1)
