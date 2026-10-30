"""Job bookkeeping, persisted system state (safe mode), source health and notifications."""
import logging
import traceback
from contextlib import contextmanager
from datetime import date

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine

from aqt.db import session_scope
from aqt.models import DataSourceStatus, JobRun, Notification, SystemLog, SystemState
from aqt.models.base import utcnow

log = logging.getLogger("aqt")


class JobContext:
    def __init__(self, run_id: int):
        self.run_id = run_id
        self.result: dict = {}
        self.status = "SUCCESS"

    def partial(self, why: str) -> None:
        self.status = "PARTIAL"
        self.result.setdefault("warnings", []).append(why)


@contextmanager
def job_run(engine: Engine, name: str, params: dict | None = None):
    with session_scope(engine) as s:
        jr = JobRun(job_name=name, params={k: str(v) for k, v in (params or {}).items()})
        s.add(jr)
        s.flush()
        ctx = JobContext(jr.id)
    try:
        yield ctx
    except Exception as e:
        with session_scope(engine) as s:
            jr = s.get(JobRun, ctx.run_id)
            jr.status, jr.finished_at, jr.result = "FAILED", utcnow(), ctx.result
            jr.error = "".join(traceback.format_exception(e))[-8000:]
        log_event(engine, "ERROR", name, f"job failed: {e}")
        notify(engine, "ERROR", f"排程工作失敗：{name}", str(e)[:2000])
        raise
    else:
        with session_scope(engine) as s:
            jr = s.get(JobRun, ctx.run_id)
            jr.status, jr.finished_at, jr.result = ctx.status, utcnow(), ctx.result


def get_state(engine: Engine, key: str, default=None):
    with session_scope(engine) as s:
        row = s.get(SystemState, key)
        return row.value if row else default


def set_state(engine: Engine, key: str, value: dict) -> None:
    with session_scope(engine) as s:
        stmt = insert(SystemState).values(key=key, value=value, updated_at=utcnow())
        s.execute(stmt.on_conflict_do_update(index_elements=["key"],
                                             set_={"value": stmt.excluded.value, "updated_at": utcnow()}))


def enter_safe_mode(engine: Engine, reason: str) -> None:
    prev = get_state(engine, "safe_mode", {}) or {}
    set_state(engine, "safe_mode", {"on": True, "reason": reason,
                                    "since": prev.get("since") if prev.get("on") else utcnow().isoformat()})
    if not prev.get("on"):
        notify(engine, "ERROR", "系統進入安全模式（暫停模擬交易）", reason)
    log_event(engine, "ERROR", "safe_mode", reason)


def exit_safe_mode(engine: Engine, reason: str) -> None:
    prev = get_state(engine, "safe_mode", {}) or {}
    if prev.get("on"):
        set_state(engine, "safe_mode", {"on": False, "reason": reason, "since": utcnow().isoformat()})
        notify(engine, "INFO", "系統解除安全模式", reason)


def is_safe_mode(engine: Engine) -> bool:
    return bool((get_state(engine, "safe_mode", {}) or {}).get("on"))


def record_source(engine: Engine, source: str, dataset: str, ok: bool, error: str | None = None,
                  data_date: date | None = None) -> None:
    now = utcnow()
    with session_scope(engine) as s:
        row = s.get(DataSourceStatus, (source, dataset))
        if row is None:
            row = DataSourceStatus(source=source, dataset=dataset, consecutive_failures=0)
            s.add(row)
        if ok:
            row.status, row.last_success_at, row.consecutive_failures = "OK", now, 0
            if data_date and (row.latest_data_date is None or data_date > row.latest_data_date):
                row.latest_data_date = data_date
        else:
            row.consecutive_failures = (row.consecutive_failures or 0) + 1
            row.status = "DOWN" if row.consecutive_failures >= 3 else "DEGRADED"
            row.last_failure_at, row.last_error = now, (error or "")[:4000]


def log_event(engine: Engine, level: str, component: str, message: str, detail: dict | None = None) -> None:
    getattr(log, level.lower(), log.info)(f"[{component}] {message}")
    with session_scope(engine) as s:
        s.add(SystemLog(level=level, component=component, message=message[:4000], detail=detail or {}))


def notify(engine: Engine, level: str, title: str, body: str = "") -> None:
    """Dashboard notification (PHASE 5 adds e-mail / LINE / Telegram delivery)."""
    with session_scope(engine) as s:
        s.add(Notification(level=level, title=title[:200], body=body))
