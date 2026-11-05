"""Row count and content fingerprint of every table (`python -m aqt.cli table-audit`).

Used around backups, restores and migrations. For each table in the public schema: row count and an
order-independent content hash over its columns. Binary columns are left out (raw_payloads keeps
their sha256 next to them). With `columns_from`, an earlier audit decides which columns are hashed, so
tables that gained columns in a migration are compared on what existed before.
"""
import hashlib
import json

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

SKIP_TYPES = ("BYTEA",)
SKIP_TABLES = ("alembic_version",)


def audit_tables(engine: Engine, columns_from: dict | None = None) -> dict:
    insp = inspect(engine)
    out: dict[str, dict] = {}
    with engine.connect() as c:
        c.execute(text("SET statement_timeout = 0"))
        for table in sorted(insp.get_table_names()):
            if table in SKIP_TABLES:
                continue
            cols = [x["name"] for x in insp.get_columns(table) if str(x["type"]).upper() not in SKIP_TYPES]
            if columns_from is not None:
                if table not in columns_from:
                    out[table] = {"rows": c.execute(text(f'SELECT count(*) FROM "{table}"')).scalar(),
                                  "new_table": True}
                    continue
                cols = columns_from[table]["columns"]
            # text of each row hashed to 64 bits, then summed: independent of physical order
            row = "concat_ws('|', " + ", ".join(f'"{x}"::text' for x in cols) + ")"
            n, s = c.execute(text(f"SELECT count(*), coalesce(sum(hashtextextended({row}, 0)::numeric), 0) "
                                  f'FROM "{table}"')).one()
            out[table] = {"rows": n, "columns": cols,
                          "content_hash": hashlib.sha256(f"{n}:{s}".encode()).hexdigest()}
    return out


def compare(before: dict, after: dict) -> dict:
    """Tables missing, or with other rows or content, than the reference audit."""
    problems = []
    for t, b in before.items():
        a = after.get(t)
        if a is None:
            problems.append(f"{t}: missing")
        elif a["rows"] != b["rows"]:
            problems.append(f"{t}: rows {b['rows']} -> {a['rows']}")
        elif a.get("content_hash") != b.get("content_hash"):
            problems.append(f"{t}: content differs")
    return {"tables_compared": len(before), "rows_compared": sum(b["rows"] for b in before.values()),
            "problems": problems, "identical": not problems,
            "new_tables": sorted(t for t in after if t not in before)}


def main_compare(before_path: str, after_path: str) -> dict:
    with open(before_path, encoding="utf-8") as fh:
        b = json.load(fh)
    with open(after_path, encoding="utf-8") as fh:
        a = json.load(fh)
    return compare(b, a)
