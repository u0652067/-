"""Strategy research registry: a candidate is written down before it is evaluated.

`register_candidate` freezes the hypothesis, the parameters (an immutable strategy version) and the
evaluation plan with their timestamp, so what was planned can be told apart from what was found.
Every registered version starts at CANDIDATE. Registration says nothing about whether it works.
"""
import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from aqt.models import ResearchProposal, StrategyStageChange, StrategyVersion
from aqt.research.records import strategy_version_id
from aqt.research.strategies import make


@dataclass(frozen=True)
class EvaluationPlan:
    """Fixed before the first evaluation run. The benchmark must be comparable for the evidence to count."""
    start: str
    end: str
    markets: tuple[str, ...] = ("TWSE",)
    train_days: int = 500
    test_days: int = 120
    grid: tuple[tuple[tuple[str, object], ...], ...] = ()

    def to_json(self) -> str:
        return json.dumps({"start": self.start, "end": self.end, "markets": list(self.markets),
                           "train_days": self.train_days, "test_days": self.test_days,
                           "grid": [dict(g) for g in self.grid]}, ensure_ascii=False, sort_keys=True)


def register_candidate(session: Session, code: str, params: dict, hypothesis: str, weakness: str,
                       plan: EvaluationPlan) -> dict:
    strategy = make(code, **params)
    vid = strategy_version_id(session, strategy, change_reason=f"research candidate: {hypothesis}"[:500])
    existing = session.scalar(select(ResearchProposal).where(ResearchProposal.candidate_version_id == vid))
    if existing is not None:
        if existing.plan != plan.to_json() or existing.hypothesis != hypothesis:
            raise ValueError("this version is already registered with a different plan: register a new version")
        return {"strategy_version_id": vid, "proposal_id": existing.id, "created": False}
    prop = ResearchProposal(weakness=weakness, hypothesis=hypothesis, plan=plan.to_json(), status="TESTING",
                            candidate_version_id=vid)
    session.add(prop)
    session.add(StrategyStageChange(strategy_version_id=vid, from_stage=None, to_stage="CANDIDATE",
                                    reason="registered before evaluation",
                                    evidence={"hypothesis": hypothesis, "plan": json.loads(plan.to_json())}))
    session.flush()
    return {"strategy_version_id": vid, "proposal_id": prop.id, "created": True}


def registry_listing(session: Session) -> list[dict]:
    rows = session.execute(select(StrategyVersion, ResearchProposal)
                           .join(ResearchProposal, ResearchProposal.candidate_version_id == StrategyVersion.id)
                           .order_by(StrategyVersion.id)).all()
    return [{"strategy_version_id": v.id, "version": v.version, "stage": v.stage, "params": v.params,
             "hypothesis": p.hypothesis, "plan": json.loads(p.plan), "registered_at": str(p.created_at)}
            for v, p in rows]
