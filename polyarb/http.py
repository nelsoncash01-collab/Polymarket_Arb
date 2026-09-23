"""HTTP fetching with retries, plus record/replay for offline runs and backtests."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit

log = logging.getLogger(__name__)


class Fetcher(Protocol):
    def get_json(self, url: str, params: dict | None = None) -> Any: ...


def cache_key(url: str, params: dict | None) -> str:
    q = urlencode(sorted((params or {}).items()), doseq=True)
    return hashlib.sha1(f"{url}?{q}".encode()).hexdigest()[:20]


class ClientError(Exception):
    """4xx other than 429: retrying won't help."""

    def __init__(self, status: int, url: str):
        super().__init__(f"HTTP {status} for {url}")
        self.status = status


class HttpFetcher:
    def __init__(self, timeout: float = 20.0, retries: int = 6, min_interval: float = 0.25,
                 record_dir: str | Path | None = None):
        import requests

        self.session = requests.Session()
        self.session.headers["User-Agent"] = "polyarb/0.1"
        self.timeout = timeout
        self.retries = retries
        self.min_interval = min_interval  # per-host spacing; Kalshi 429s on bursts
        self.record_dir = Path(record_dir) if record_dir else None
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
        self._last: dict[str, float] = {}

    def get_json(self, url: str, params: dict | None = None) -> Any:
        host = urlsplit(url).netloc
        delay = 1.0
        for attempt in range(self.retries + 1):
            wait = self.min_interval - (time.monotonic() - self._last.get(host, 0.0))
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.monotonic()
            try:
                r = self.session.get(url, params=params, timeout=self.timeout)
                if r.status_code == 429 or r.status_code >= 500:
                    retry_after = r.headers.get("Retry-After", "")
                    if retry_after.replace(".", "", 1).isdigit():
                        delay = max(delay, float(retry_after))
                    raise RuntimeError(f"HTTP {r.status_code}")
                if r.status_code >= 400:
                    self._record(url, params, {"__client_error__": r.status_code})
                    raise ClientError(r.status_code, r.url)
                data = r.json()
                self._record(url, params, data)
                return data
            except ClientError:
                raise
            except Exception as exc:  # noqa: BLE001 - retry anything transient
                if attempt == self.retries:
                    raise
                log.warning("GET %s failed (%s); retry in %.0fs", url, exc, delay)
                time.sleep(delay)
                delay = min(delay * 2, 30.0)


    def _record(self, url: str, params: dict | None, data: Any) -> None:
        if self.record_dir:
            path = self.record_dir / f"{cache_key(url, params)}.json"
            path.write_text(json.dumps({"url": url, "params": params, "data": data}))


class ReplayFetcher:
    """Serves responses recorded by HttpFetcher(record_dir=...)."""

    def __init__(self, replay_dir: str | Path):
        self.dir = Path(replay_dir)

    def get_json(self, url: str, params: dict | None = None) -> Any:
        path = self.dir / f"{cache_key(url, params)}.json"
        if not path.exists():
            raise FileNotFoundError(f"no recording for {url} {params}")
        data = json.loads(path.read_text())["data"]
        if isinstance(data, dict) and "__client_error__" in data:
            raise ClientError(data["__client_error__"], url)
        return data
