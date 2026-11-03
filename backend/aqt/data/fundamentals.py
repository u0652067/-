"""Point-in-time access to financial statements for research / backtests (G7).

Only statements with a reliable publication time (`pit_reliable`) that were public at the decision
time are returned. Statements whose publication time could not be established (published_basis
STATUTORY_DEADLINE / UNKNOWN) are never returned here, so fundamental strategies cannot use them.
"""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from aqt.models import FinancialStatement


def latest_statements_as_of(session: Session, as_of: datetime, security_ids: list[int] | None = None,
                            basis: str = "CUMULATIVE") -> dict[int, FinancialStatement]:
    """Most recent (by fiscal period) statement per security that was public at `as_of`."""
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    q = (select(FinancialStatement)
         .where(FinancialStatement.pit_reliable.is_(True), FinancialStatement.available_at <= as_of,
                FinancialStatement.basis == basis)
         .order_by(FinancialStatement.security_id, FinancialStatement.fiscal_year.desc(),
                   FinancialStatement.fiscal_quarter.desc()))
    if security_ids is not None:
        q = q.where(FinancialStatement.security_id.in_(security_ids))
    out: dict[int, FinancialStatement] = {}
    for fs in session.scalars(q):
        out.setdefault(fs.security_id, fs)
    return out
