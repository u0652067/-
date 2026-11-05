"""Deployment safety: no implicit migration, refusal on schema mismatch, rollback policy, table audit."""
import json

import pytest
from sqlalchemy import text

from aqt import cli, schema_guard, table_audit
from aqt.db import session_scope
from aqt.models import DataQualityIssue
from aqt.models.base import utcnow


def _set_version(engine, v):
    with engine.begin() as c:
        c.execute(text("UPDATE alembic_version SET version_num=:v"), {"v": v})


def test_matching_schema_passes_and_status_reports_the_build(engine):
    st = schema_guard.require_schema(engine)
    assert st["match"] is True and st["database_version"] == st["build_expects"] == schema_guard.expected_head()


@pytest.mark.parametrize("old", ["0003", "0004"])
def test_mismatch_refuses_with_a_clear_message_and_changes_nothing(engine, old):
    _set_version(engine, old)
    with pytest.raises(schema_guard.SchemaMismatch, match="never run automatically"):
        schema_guard.require_schema(engine)
    assert schema_guard.current_version(engine) == old  # no silent upgrade


def test_empty_database_is_a_mismatch(engine):
    with engine.begin() as c:
        c.execute(text("DROP TABLE alembic_version"))
    assert schema_guard.current_version(engine) is None
    with pytest.raises(schema_guard.SchemaMismatch):
        schema_guard.require_schema(engine)


@pytest.mark.parametrize("cmd", [["scheduler"], ["api"], ["update-daily"], ["sync-securities"],
                                 ["backtest", "--strategy", "cash_baseline", "--start", "2024-01-01",
                                  "--end", "2024-02-01"]])
def test_commands_refuse_to_start_on_a_stale_schema(engine, monkeypatch, cmd):
    monkeypatch.setattr("aqt.db.get_engine", lambda *a, **k: engine)
    _set_version(engine, "0003")
    with pytest.raises(SystemExit) as e:
        cli.main(cmd)
    assert "never run automatically" in str(e.value)


def test_init_db_needs_the_explicit_flag(engine):
    _set_version(engine, "0003")
    with pytest.raises(SystemExit) as e:
        cli.main(["init-db"])
    assert "refused" in str(e.value) and schema_guard.current_version(engine) == "0003"


def test_rollback_is_allowed_only_before_any_audit_record(engine):
    r = schema_guard.rollback_check(engine)
    assert r["downgrade_allowed"] is True and set(r["downgrade_would_lose"]) == {"0004", "0005", "0005a"}
    assert any("backtest_run_invalidations" in x for x in r["downgrade_would_lose"]["0005"])
    with session_scope(engine) as s:
        s.add(DataQualityIssue(check_name="manual_review", severity="ERROR", dataset="daily_prices", detail={},
                               known_at=utcnow(), effective_at=utcnow(), detection_method="POST_HOC_MANUAL"))
    r = schema_guard.rollback_check(engine)
    assert r["downgrade_allowed"] is False and "restore the pre-migration backup" in r["standard_rollback"]
    assert r["audit_records"]["post_hoc_quality_issues"] == 1


def test_rollback_blocked_by_a_backtest_record(engine):
    with engine.begin() as c:
        c.execute(text("INSERT INTO strategies (code, name, horizon, description, created_at) "
                       "VALUES ('x','x','SWING','',now())"))
        c.execute(text("INSERT INTO strategy_versions (strategy_id, version, params, change_reason, stage, created_at) "
                       "SELECT id,'V1','{}','t','CANDIDATE',now() FROM strategies"))
        c.execute(text("INSERT INTO backtest_runs (strategy_version_id, kind, period_start, period_end, universe_rule, "
                       "cost_model, metrics, regime_metrics, equity_curve, data_snapshot_at, status, created_at) "
                       "SELECT id,'IN_SAMPLE','2024-01-01','2024-02-01','u','{}','{}','{}','[]',now(),'DONE',now() "
                       "FROM strategy_versions"))
    r = schema_guard.rollback_check(engine)
    assert r["downgrade_allowed"] is False and r["audit_records"]["backtest_runs"] == 1


def test_table_audit_covers_every_table_and_detects_one_changed_value(engine, tmp_path):
    with session_scope(engine) as s:
        s.add(DataQualityIssue(check_name="a", severity="WARN", dataset="d", detail={}, known_at=utcnow(),
                               effective_at=utcnow()))
    a = table_audit.audit_tables(engine)
    assert "raw_payloads" in a and "content_gz" not in a["raw_payloads"]["columns"]
    assert "backtest_runs" in a and len(a) > 40
    assert table_audit.compare(a, table_audit.audit_tables(engine))["identical"] is True
    with engine.begin() as c:
        c.execute(text("UPDATE data_quality_issues SET check_name='b'"))
    out = table_audit.compare(a, table_audit.audit_tables(engine))
    assert out["identical"] is False and out["problems"] == ["data_quality_issues: content differs"]
    p1, p2 = tmp_path / "a.json", tmp_path / "b.json"
    p1.write_text(json.dumps(a), encoding="utf-8")
    p2.write_text(json.dumps(a), encoding="utf-8")
    assert table_audit.main_compare(str(p1), str(p2))["identical"] is True


def test_table_audit_compares_old_columns_after_a_migration_added_some(engine):
    before = table_audit.audit_tables(engine)
    with engine.begin() as c:
        c.execute(text("ALTER TABLE notifications ADD COLUMN extra text DEFAULT 'x'"))
        c.execute(text("CREATE TABLE brand_new (id int)"))
    after = table_audit.audit_tables(engine, columns_from=before)
    out = table_audit.compare(before, after)
    assert out["identical"] is True and out["new_tables"] == ["brand_new"]


def test_health_reports_build_and_schema(engine, monkeypatch):
    from fastapi.testclient import TestClient

    from aqt.api import main as api
    monkeypatch.setenv("AQT_BUILD_COMMIT", "abc123")
    monkeypatch.setattr(api, "get_engine", lambda *a, **k: engine, raising=False)
    client = TestClient(api.app)
    body = client.get("/api/health").json()
    assert body["build_commit"] == "abc123" and body["schema"]["match"] is True
