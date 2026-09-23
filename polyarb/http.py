"""HTTP fetching with retries, plus record/replay for offline runs and backtests."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode

log = logging.getLogger(__name__)


class Fetcher(Protocol):
    def get_json(self, url: str, params: dict | None = None) -> Any: ...


def cache_key(url: str, params: dict | None) -> str:
    q = urlencode(sorted((params or {}).items()), doseq=True)
    return hashlib.sha1(f"{url}?{q}".encode()).hexdigest()[:20]


class HttpFetcher:
    def __init__(self, timeout: float = 15.0, retries: int = 4, min_interval: float = 0.12,
                 record_dir: str | Path | None = None):
        import requests

        self.session = requests.Session()
        self.session.headers["User-Agent"] = "polyarb/0.1"
        self.timeout = timeout
        self.retries = retries
        self.min_interval = min_interval  # crude client-side rate limit
        self.record_dir = Path(record_dir) if record_dir else None
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
        self._last = 0.0

    def get_json(self, url: str, params: dict | None = None) -> Any:
        delay = 1.0
        for attempt in range(self.retries + 1):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                r = self.session.get(url, params=params, timeout=self.timeout)
                if r.status_code == 429 or r.status_code >= 500:
                    raise RuntimeError(f"HTTP {r.status_code}")
                r.raise_for_status()
                data = r.json()
                if self.record_dir:
                    path = self.record_dir / f"{cache_key(url, params)}.json"
                    path.write_text(json.dumps({"url": url, "params": params, "data": data}))
                return data
            except Exception as exc:  # noqa: BLE001 - retry anything transient
                if attempt == self.retries:
                    raise
                log.warning("GET %s failed (%s); retry in %.0fs", url, exc, delay)
                time.sleep(delay)
                delay *= 2


class ReplayFetcher:
    """Serves responses recorded by HttpFetcher(record_dir=...)."""

    def __init__(self, replay_dir: str | Path):
        self.dir = Path(replay_dir)

    def get_json(self, url: str, params: dict | None = None) -> Any:
        path = self.dir / f"{cache_key(url, params)}.json"
        if not path.exists():
            raise FileNotFoundError(f"no recording for {url} {params}")
        return json.loads(path.read_text())["data"]
