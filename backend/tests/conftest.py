import json
import os
from datetime import date

import httpx
import pytest

TEST_DB = os.environ.get("AQT_TEST_DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:5432/aqt_test")
os.environ["AQT_DATABASE_URL"] = TEST_DB
os.environ["AQT_HTTP_MIN_INTERVAL_SECONDS"] = "0"
os.environ["AQT_HTTP_MAX_RETRIES"] = "2"

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from aqt.data.http import Fetcher  # noqa: E402
from tests.fixtures.factory import Market  # noqa: E402

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _migrate(engine):
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = Config(os.path.join(BACKEND, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND, "alembic"))
    cfg.attributes["database_url"] = TEST_DB
    command.upgrade(cfg, "head")


@pytest.fixture()
def engine():
    eng = create_engine(TEST_DB, future=True)
    _migrate(eng)
    yield eng
    eng.dispose()


class Router:
    """httpx MockTransport that serves synthetic payloads by URL path; records calls; can inject failures."""

    def __init__(self, market: Market):
        self.m = market
        self.calls: list[str] = []
        self.fail: dict[str, int] = {}  # path fragment -> HTTP status
        self.override: dict[str, dict] = {}  # path fragment -> payload

    @staticmethod
    def _d(q, key, fmt):
        from datetime import datetime
        return datetime.strptime(q[key], fmt).date()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, q = request.url.path, dict(request.url.params)
        self.calls.append(f"{path}?{request.url.query.decode()}")
        for frag, status in self.fail.items():
            if frag in path:
                return httpx.Response(status, text="boom")
        for frag, payload in self.override.items():
            if frag in path:
                return httpx.Response(200, json=payload)
        m = self.m
        if path.endswith("/afterTrading/MI_INDEX"):
            body = m.twse_mi_index(self._d(q, "date", "%Y%m%d"))
        elif path.endswith("/fund/T86"):
            body = m.twse_t86(self._d(q, "date", "%Y%m%d"))
        elif path.endswith("/marginTrading/MI_MARGN"):
            body = m.twse_margin(self._d(q, "date", "%Y%m%d"))
        elif path.endswith("/afterTrading/BWIBBU_d"):
            body = m.twse_bwibbu(self._d(q, "date", "%Y%m%d"))
        elif path.endswith("/exRight/TWT49U"):
            body = m.twse_twt49u(self._d(q, "startDate", "%Y%m%d"), self._d(q, "endDate", "%Y%m%d"))
        elif path.endswith("/afterTrading/dailyQ"):
            body = m.tpex_daily(self._d(q, "date", "%Y/%m/%d"))
        elif path.endswith("/insti/dailyTrade"):
            body = m.tpex_insti(self._d(q, "date", "%Y/%m/%d"))
        elif path.endswith("/margin/balance"):
            body = m.tpex_margin(self._d(q, "date", "%Y/%m/%d"))
        elif path.endswith("/afterTrading/peQryDate"):
            body = m.tpex_pe(self._d(q, "date", "%Y/%m/%d"))
        else:
            return httpx.Response(404, text="unknown")
        return httpx.Response(200, content=json.dumps(body, ensure_ascii=False).encode("utf-8"))


@pytest.fixture()
def synthetic_market():
    return Market(date(2024, 1, 2), days=100, ex_div=("2317", 70, 5.0))


@pytest.fixture()
def router(synthetic_market):
    return Router(synthetic_market)


@pytest.fixture()
def fetcher(engine, router):
    f = Fetcher(engine, transport=httpx.MockTransport(router), sleep=lambda s: None)
    yield f
    f.close()


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def load_fixture(name: str):
    path = os.path.join(FIXTURES, name)
    if name.endswith(".json"):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    with open(path, "rb") as fh:
        return fh.read()
