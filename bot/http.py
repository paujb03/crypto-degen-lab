"""HTTP helper with retries, per-host rate limiting and graceful failure."""
from __future__ import annotations

import time
from urllib.parse import urlparse

import requests

_session = requests.Session()
_session.headers.update({"User-Agent": "crypto-degen-lab/1.0", "Accept": "application/json"})

# Minimum seconds between requests to the same host (free tiers are strict)
_MIN_INTERVAL = {
    "api.geckoterminal.com": 2.1,   # ~30 req/min
    "api.coingecko.com": 2.5,
    "api.dexscreener.com": 0.25,
    "api.rugcheck.xyz": 0.6,
    "api.gopluslabs.io": 1.0,
}
_last_call: dict[str, float] = {}

errors: list[str] = []


def get_json(url: str, params: dict | None = None, retries: int = 3, timeout: int = 20):
    """GET a JSON document. Returns None on failure (and records the error)."""
    host = urlparse(url).netloc
    for attempt in range(retries):
        wait = _MIN_INTERVAL.get(host, 0.3) - (time.time() - _last_call.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        _last_call[host] = time.time()
        try:
            r = _session.get(url, params=params, timeout=timeout)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code != 200:
                errors.append(f"{host} {r.status_code} {url}")
                return None
            return r.json()
        except (requests.RequestException, ValueError) as e:
            if attempt == retries - 1:
                errors.append(f"{host} {type(e).__name__}: {e}")
            time.sleep(1.5 * (attempt + 1))
    return None
