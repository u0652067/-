"""audit triggers: append-only + hash-chained ledgers, immutable strategy versions

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

HASH_CHAINED = ["fills", "cash_ledger", "order_events", "trade_decisions"]
APPEND_ONLY = HASH_CHAINED + ["reviews", "strategy_stage_changes", "account_settings_history", "raw_payloads"]


def upgrade() -> None:
    op.execute("""
    CREATE OR REPLACE FUNCTION aqt_forbid_change() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'table % is append-only: % is not allowed', TG_TABLE_NAME, TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END $$ LANGUAGE plpgsql;
    """)
    # raw_payloads: parse_status / parse_error may be updated, content may not.
    op.execute("""
    CREATE OR REPLACE FUNCTION aqt_raw_payload_guard() RETURNS trigger AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'raw_payloads rows cannot be deleted' USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.content_gz IS DISTINCT FROM OLD.content_gz OR NEW.sha256 IS DISTINCT FROM OLD.sha256
           OR NEW.fetched_at IS DISTINCT FROM OLD.fetched_at OR NEW.url IS DISTINCT FROM OLD.url THEN
            RAISE EXCEPTION 'raw payload content is immutable' USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END $$ LANGUAGE plpgsql;
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION aqt_hash_chain() RETURNS trigger AS $$
    DECLARE prev text;
    BEGIN
        PERFORM pg_advisory_xact_lock(hashtext('aqt_chain_' || TG_TABLE_NAME));
        EXECUTE format('SELECT row_hash FROM %I ORDER BY id DESC LIMIT 1', TG_TABLE_NAME) INTO prev;
        NEW.prev_hash := COALESCE(prev, repeat('0', 64));
        NEW.row_hash := NULL;
        NEW.row_hash := encode(sha256(convert_to(NEW.prev_hash || row_to_json(NEW)::text, 'UTF8')), 'hex');
        RETURN NEW;
    END $$ LANGUAGE plpgsql;
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION aqt_strategy_version_guard() RETURNS trigger AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'strategy versions are never deleted (retire them instead)'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.params IS DISTINCT FROM OLD.params OR NEW.version IS DISTINCT FROM OLD.version
           OR NEW.strategy_id IS DISTINCT FROM OLD.strategy_id OR NEW.change_reason IS DISTINCT FROM OLD.change_reason
           OR NEW.parent_version_id IS DISTINCT FROM OLD.parent_version_id OR NEW.code_ref IS DISTINCT FROM OLD.code_ref THEN
            RAISE EXCEPTION 'strategy version definition is immutable; create a new version'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END $$ LANGUAGE plpgsql;
    """)
    # Orders: status/fill progress may change; the decision record (reason, prices, qty) may not; never deleted.
    op.execute("""
    CREATE OR REPLACE FUNCTION aqt_order_guard() RETURNS trigger AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'orders are never deleted' USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.reason IS DISTINCT FROM OLD.reason OR NEW.quantity IS DISTINCT FROM OLD.quantity
           OR NEW.limit_price IS DISTINCT FROM OLD.limit_price OR NEW.side IS DISTINCT FROM OLD.side
           OR NEW.security_id IS DISTINCT FROM OLD.security_id OR NEW.decision_id IS DISTINCT FROM OLD.decision_id
           OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'order decision fields are immutable' USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END $$ LANGUAGE plpgsql;
    """)
    for t in APPEND_ONLY:
        if t == "raw_payloads":
            op.execute(f"CREATE TRIGGER {t}_guard BEFORE UPDATE OR DELETE ON {t} "
                       f"FOR EACH ROW EXECUTE FUNCTION aqt_raw_payload_guard()")
        else:
            op.execute(f"CREATE TRIGGER {t}_append_only BEFORE UPDATE OR DELETE ON {t} "
                       f"FOR EACH ROW EXECUTE FUNCTION aqt_forbid_change()")
        op.execute(f"CREATE TRIGGER {t}_no_truncate BEFORE TRUNCATE ON {t} "
                   f"FOR EACH STATEMENT EXECUTE FUNCTION aqt_forbid_change()")
    for t in HASH_CHAINED:
        op.execute(f"CREATE TRIGGER {t}_hash_chain BEFORE INSERT ON {t} "
                   f"FOR EACH ROW EXECUTE FUNCTION aqt_hash_chain()")
    op.execute("CREATE TRIGGER strategy_versions_guard BEFORE UPDATE OR DELETE ON strategy_versions "
               "FOR EACH ROW EXECUTE FUNCTION aqt_strategy_version_guard()")
    op.execute("CREATE TRIGGER orders_guard BEFORE UPDATE OR DELETE ON orders "
               "FOR EACH ROW EXECUTE FUNCTION aqt_order_guard()")


def downgrade() -> None:
    for t in APPEND_ONLY:
        op.execute(f"DROP TRIGGER IF EXISTS {t}_append_only ON {t}")
        op.execute(f"DROP TRIGGER IF EXISTS {t}_guard ON {t}")
        op.execute(f"DROP TRIGGER IF EXISTS {t}_no_truncate ON {t}")
    for t in HASH_CHAINED:
        op.execute(f"DROP TRIGGER IF EXISTS {t}_hash_chain ON {t}")
    op.execute("DROP TRIGGER IF EXISTS strategy_versions_guard ON strategy_versions")
    op.execute("DROP TRIGGER IF EXISTS orders_guard ON orders")
    for fn in ["aqt_forbid_change", "aqt_raw_payload_guard", "aqt_hash_chain",
               "aqt_strategy_version_guard", "aqt_order_guard"]:
        op.execute(f"DROP FUNCTION IF EXISTS {fn}()")
