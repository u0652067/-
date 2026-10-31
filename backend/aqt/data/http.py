"""Polite HTTP fetcher that archives every raw response before anything parses it."""
import gzip
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from urllib.parse import urlparse

import httpx
from sqlalchemy.engine import Engine

from aqt.config import Settings, get_settings
from aqt.db import session_scope
from aqt.models import RawPayload

log = logging.getLogger(__name__)


class FetchError(RuntimeError):
    pass


class SourceBlockedError(FetchError):
    """Upstream refused us (rate limit / WAF / network policy). Never retried aggressively."""


@dataclass
class FetchResult:
    raw_id: int | None
    url: str
    status: int
    content: bytes
    fetched_at: datetime

    def json(self):
        text = self.content.decode("utf-8-sig", errors="replace").strip()
        if not text or text[0] not in "[{":
            raise FetchError(f"non-JSON response from {self.url}: {text[:120]!r}")
        return json.loads(text)


class Fetcher:
    def __init__(self, engine: Engine | None = None, settings: Settings | None = None,
                 transport: httpx.BaseTransport | None = None, archive: bool = True,
                 sleep=time.sleep):
        self.settings = settings or get_settings()
        self.engine = engine
        self.archive = archive
        self._sleep = sleep
        self._last_hit: dict[str, float] = {}
        self.client = httpx.Client(
            timeout=self.settings.http_timeout_seconds, transport=transport, follow_redirects=True,
            headers={"User-Agent": self.settings.http_user_agent, "Accept": "application/json,text/html,*/*"},
        )

    def _throttle(self, host: str) -> None:
        gap = self.settings.http_min_interval_seconds
        last = self._last_hit.get(host)
        if last is not None:
            wait = gap - (time.monotonic() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_hit[host] = time.monotonic()

    def get(self, source: str, dataset: str, url: str, params: dict | None = None,
            data_date: date | None = None) -> FetchResult:
        host = urlparse(url).netloc
        last_exc: Exception | None = None
        for attempt in range(self.settings.http_max_retries):
            self._throttle(host)
            try:
                resp = self.client.get(url, params=params)
            except httpx.ProxyError as e:  # egress policy denial
                raise SourceBlockedError(f"{host}: proxy refused connection ({e})") from e
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_exc = e
                self._sleep(2 ** attempt * 2)
                continue
            if resp.status_code in (403, 407):
                raise SourceBlockedError(f"{host}: HTTP {resp.status_code}")
            if b"FOR SECURITY REASONS" in resp.content[:2000]:
                # TWSE firewall notice (served as 307 or 200) after too many requests: back off, don't hammer
                raise SourceBlockedError(f"{host}: security notice page (rate limited / refused)")
            # an unresolved 3xx without the notice page is treated as transient
            if resp.status_code == 429 or resp.status_code >= 500 or 300 <= resp.status_code < 400:
                last_exc = FetchError(f"{host}: HTTP {resp.status_code}")
                self._sleep(2 ** attempt * 5)
                continue
            fetched_at = datetime.now(timezone.utc)
            raw_id = self._archive(source, dataset, str(resp.url), params or {}, data_date,
                                   resp.status_code, resp.content, fetched_at)
            if resp.status_code != 200:
                raise FetchError(f"{host}: HTTP {resp.status_code}")
            return FetchResult(raw_id, str(resp.url), resp.status_code, resp.content, fetched_at)
        raise FetchError(f"{url} failed after {self.settings.http_max_retries} attempts: {last_exc}")

    def _archive(self, source, dataset, url, params, data_date, status, content, fetched_at) -> int | None:
        if not self.archive or self.engine is None:
            return None
        with session_scope(self.engine) as s:
            row = RawPayload(source=source, dataset=dataset, url=url, params=params, data_date=data_date,
                             fetched_at=fetched_at, http_status=status,
                             sha256=hashlib.sha256(content).hexdigest(), content_gz=gzip.compress(content))
            s.add(row)
            s.flush()
            return row.id

    def close(self) -> None:
        self.client.close()


def mark_parsed(engine: Engine, raw_id: int | None, status: str, error: str | None = None) -> None:
    if raw_id is None:
        return
    with session_scope(engine) as s:
        row = s.get(RawPayload, raw_id)
        row.parse_status = status
        row.parse_error = error


def load_raw(engine: Engine, raw_id: int) -> bytes:
    with session_scope(engine) as s:
        return gzip.decompress(s.get(RawPayload, raw_id).content_gz)
