"""Sign the running app in to TUM-Live with TUM_USERNAME/TUM_PASSWORD from .env (local testing).

Prints only the outcome - never the credentials. Usage: python scripts/login_from_env.py [sso|internal]
"""
import json
import sys
import urllib.request
from pathlib import Path

env = {}
for line in (Path(__file__).resolve().parent.parent / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip("'\"")
body = json.dumps({"method": sys.argv[1] if len(sys.argv) > 1 else "sso", "username": env["TUM_USERNAME"],
                   "password": env["TUM_PASSWORD"], "remember": True}).encode()
req = urllib.request.Request("http://127.0.0.1:8420/api/session/login", data=body,
                             headers={"Content-Type": "application/json", "X-Studio": "1"})
with urllib.request.urlopen(req, timeout=90) as r:
    res = json.load(r)
print({k: res.get(k) for k in ("state", "message", "inputs", "choices") if res.get(k)} |
      ({"signed_in": bool(res.get("display_name"))} if res.get("state") == "authenticated" else {}))
