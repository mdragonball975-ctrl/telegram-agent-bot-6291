"""Docker HEALTHCHECK probe: exits 0 when /health returns 200."""
import sys
import urllib.request

from config import _int

try:
    port = _int("PORT", 8080)
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=8) as r:
        sys.exit(0 if r.status == 200 else 1)
except Exception:
    sys.exit(1)
