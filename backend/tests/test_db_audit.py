"""The database itself enforces the audit rules (no deleting failed trades, no rewriting reasons)."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError


def _seed(c):
    c.execute(text("INSERT INTO securities (code, market, name, security_type, status, created_at, updated_at) "
                   "VALUES ('2330','TWSE','台積電','STOCK','ACTIVE', now(), now())"))
    c.execute(text("INSERT INTO sim_accounts (name, mode, initial_capital, status, created_at) "
                   "VALUES ('main','SIMULATION',1000000,'STOPPED', now())"))


def test_simulation_only_constraint(engine):
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(text("INSERT INTO sim_accounts (name, mode, initial_capital, status, created_at) "
                           "VALUES ('x','LIVE',1000,'STOPPED', now())"))


def test_decisions_are_append_only_and_hash_chained(engine):
    with engine.begin() as c:
        _seed(c)
        for i in range(3):
            c.execute(text("INSERT INTO trade_decisions (account_id, decided_at, data_cutoff_at, security_id, action, "
                           "reasoning, analyst_inputs, risk_check, approved) VALUES (1, now(), now(), 1, 'BUY', :r, "
                           "'{}', '{}', true)"), {"r": f"reason {i}"})
    with engine.connect() as c:
        rows = c.execute(text("SELECT id, prev_hash, row_hash FROM trade_decisions ORDER BY id")).all()
    assert rows[0].prev_hash == "0" * 64
    assert rows[1].prev_hash == rows[0].row_hash and rows[2].prev_hash == rows[1].row_hash
    assert len({r.row_hash for r in rows}) == 3

    for stmt in ("UPDATE trade_decisions SET reasoning='rewritten' WHERE id=1",
                 "DELETE FROM trade_decisions WHERE id=1",
                 "TRUNCATE trade_decisions CASCADE"):
        with pytest.raises(DBAPIError, match="append-only"):
            with engine.begin() as c:
                c.execute(text(stmt))


def test_hash_chain_detects_tampering(engine):
    """Recompute the chain from row contents: an edit made with triggers disabled is detectable."""
    with engine.begin() as c:
        _seed(c)
        for i in range(3):
            c.execute(text("INSERT INTO trade_decisions (account_id, decided_at, data_cutoff_at, security_id, action, "
                           "reasoning, analyst_inputs, risk_check, approved) VALUES (1, now(), now(), 1, 'SKIP', :r, "
                           "'{}', '{}', false)"), {"r": f"r{i}"})

    def verify(c) -> bool:
        rows = c.execute(text("SELECT prev_hash, row_hash, row_to_json(t)::text AS j FROM "
                              "(SELECT * FROM trade_decisions ORDER BY id) t")).all()
        prev = "0" * 64
        for r in rows:
            recomputed = c.execute(text(
                "SELECT encode(sha256(convert_to(:p || row_to_json(x)::text,'UTF8')),'hex') FROM "
                "(SELECT * FROM json_populate_record(NULL::trade_decisions, "
                "(CAST(:j AS jsonb) || jsonb_build_object('row_hash', NULL))::json)) x"),
                {"p": prev, "j": r.j}).scalar()
            if r.prev_hash != prev or recomputed != r.row_hash:
                return False
            prev = r.row_hash
        return True

    with engine.connect() as c:
        assert verify(c)
    with engine.begin() as c:  # simulate a superuser bypassing triggers
        c.execute(text("ALTER TABLE trade_decisions DISABLE TRIGGER USER"))
        c.execute(text("UPDATE trade_decisions SET reasoning='edited later' WHERE id=2"))
        c.execute(text("ALTER TABLE trade_decisions ENABLE TRIGGER USER"))
    with engine.connect() as c:
        assert not verify(c)


def test_orders_keep_reason_but_allow_status(engine):
    with engine.begin() as c:
        _seed(c)
        c.execute(text("INSERT INTO orders (account_id, security_id, side, order_type, time_in_force, quantity, "
                       "filled_quantity, status, reason, created_at) VALUES (1,1,'BUY','LIMIT','ROD',1000,0,'NEW',"
                       "'breakout', now())"))
        c.execute(text("UPDATE orders SET status='ACCEPTED' WHERE id=1"))
    with pytest.raises(DBAPIError, match="immutable"):
        with engine.begin() as c:
            c.execute(text("UPDATE orders SET reason='better story' WHERE id=1"))
    with pytest.raises(DBAPIError, match="never deleted"):
        with engine.begin() as c:
            c.execute(text("DELETE FROM orders WHERE id=1"))


def test_strategy_versions_immutable(engine):
    with engine.begin() as c:
        c.execute(text("INSERT INTO strategies (code, name, horizon, description, created_at) "
                       "VALUES ('trend','Trend','SWING','', now())"))
        c.execute(text("INSERT INTO strategy_versions (strategy_id, version, params, change_reason, stage, created_at) "
                       "VALUES (1,'V1.0',:p,'initial','CANDIDATE', now())"), {"p": '{"ma": 20}'})
        c.execute(text("UPDATE strategy_versions SET stage='BACKTESTED' WHERE id=1"))  # stage may advance
    with pytest.raises(DBAPIError, match="immutable"):
        with engine.begin() as c:
            c.execute(text("UPDATE strategy_versions SET params=:p WHERE id=1"), {"p": '{"ma": 30}'})
    with pytest.raises(DBAPIError, match="never deleted"):
        with engine.begin() as c:
            c.execute(text("DELETE FROM strategy_versions WHERE id=1"))


def test_raw_payload_content_immutable(engine):
    with engine.begin() as c:
        c.execute(text("INSERT INTO raw_payloads (source, dataset, url, params, fetched_at, sha256, content_gz, "
                       "parse_status) VALUES ('t','d','u','{}', now(), 'x', '\\x00', 'PENDING')"))
        c.execute(text("UPDATE raw_payloads SET parse_status='OK'"))
    with pytest.raises(DBAPIError, match="immutable"):
        with engine.begin() as c:
            c.execute(text("UPDATE raw_payloads SET content_gz='\\x01'"))
