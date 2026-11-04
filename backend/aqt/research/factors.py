"""Factor / score predictive-power studies (technical data only).

For sample dates T (spaced `step` trading days, step >= horizon so periods do not overlap):
  * universe and factor values come from the point-in-time DataView of T (18:00), nothing later;
  * the outcome is the total return from the open of T+1 to the open of T+1+h, i.e. what a next-open
    entry could earn before costs. Names that open at the limit-up on T+1 cannot be bought and are left
    out (counted). A name that stops trading inside the window is kept with its return up to the last
    available close and counted as truncated — never dropped, so delistings cannot flatter the result;
  * per date: rank IC (Spearman) and mean return by quantile. Across dates: mean, t-statistic from the
    dispersion of the date values, share of positive dates, top-minus-bottom spread, monotonicity, and
    the same by year, by regime and for the earlier / later part of the sample.

`multiple_testing` applies Holm's correction across a study group: testing many factors and reading
the best one is exactly the selection bias this guards against.

Results are statistical descriptions before costs. They never establish that a factor or strategy is
effective, profitable or tradable.
"""
import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from aqt.models import CorporateAction, DailyPrice, FactorStudy
from aqt.research import DISCLAIMER
from aqt.research.dataview import ALL_MARKETS, DataView
from aqt.research.fingerprint import data_fingerprint
from aqt.research.regimes import load_regimes
from aqt.research.splits import trading_days

LOOKBACK = 70  # trading days of history handed to the factor functions


def _ret(g: pd.DataFrame, n: int) -> float:
    return g.adj_close.iloc[-1] / g.adj_close.iloc[-1 - n] - 1 if len(g) > n else float("nan")


def _ma_gap(g: pd.DataFrame, n: int) -> float:
    return g.adj_close.iloc[-1] / g.adj_close.tail(n).mean() - 1 if len(g) >= n else float("nan")


def _vol(g: pd.DataFrame, n: int = 20) -> float:
    return float(g.adj_close.pct_change().tail(n).std()) if len(g) > n else float("nan")


FACTORS = {
    "mom_20": lambda g: _ret(g, 20),
    "mom_60": lambda g: _ret(g, 60),
    "reversal_5": lambda g: -_ret(g, 5),
    "trend_ma20": lambda g: _ma_gap(g, 20),
    "trend_ma60": lambda g: _ma_gap(g, 60),
    "low_vol_20": lambda g: -_vol(g),
    "volume_surge": lambda g: (float(g.volume.tail(5).astype(float).mean()) / m - 1
                               if len(g) >= 20 and (m := float(g.volume.tail(20).astype(float).mean())) > 0
                               else float("nan")),
}
# registered composite: mean cross-sectional percentile rank of its members (higher = stronger score)
COMPOSITE_SCORE = ("mom_60", "trend_ma60", "low_vol_20")


def factor_table(view: DataView, security_ids: list[int]) -> pd.DataFrame:
    """One row per security with every factor, all computed from the point-in-time history."""
    h = view.history(security_ids, lookback_days=LOOKBACK)
    rows = []
    for sid, g in h.groupby("sid", sort=True):
        if g.d.iloc[-1] != view.as_of:
            continue
        rows.append({"sid": int(sid), **{k: fn(g) for k, fn in FACTORS.items()}})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["score_composite"] = df[list(COMPOSITE_SCORE)].rank(pct=True).mean(axis=1, skipna=False)
    return df


def forward_returns(session: Session, security_ids: list[int], entry: date, exit_: date) -> pd.DataFrame:
    """Total return entry-open -> exit-open (ex-date in (entry, exit] adjusted; costs excluded)."""
    if not security_ids:
        return pd.DataFrame(columns=["sid", "ret", "truncated", "unbuyable"])
    bars = session.execute(select(DailyPrice.security_id, DailyPrice.trade_date, DailyPrice.open, DailyPrice.close,
                                  DailyPrice.limit_up)
                           .where(DailyPrice.security_id.in_(security_ids), DailyPrice.trade_date >= entry,
                                  DailyPrice.trade_date <= exit_)
                           .order_by(DailyPrice.security_id, DailyPrice.trade_date)).all()
    events: dict[int, list] = {}
    for sid, ex, pc, ref in session.execute(select(CorporateAction.security_id, CorporateAction.ex_date,
                                                   CorporateAction.prev_close, CorporateAction.ref_price)
                                            .where(CorporateAction.security_id.in_(security_ids),
                                                   CorporateAction.ex_date > entry, CorporateAction.ex_date <= exit_)):
        if pc and ref and ref > 0:
            events.setdefault(sid, []).append((ex, float(pc / ref)))
    by: dict[int, list] = {}
    for b in bars:
        by.setdefault(b[0], []).append(b)
    rows = []
    for sid in sorted(security_ids):
        rs = by.get(sid, [])
        first = rs[0] if rs and rs[0][1] == entry else None
        if first is None or first[2] is None:
            rows.append({"sid": sid, "ret": float("nan"), "truncated": False, "unbuyable": True})
            continue
        if first[4] is not None and first[2] >= first[4]:  # limit-up at the open: cannot be bought
            rows.append({"sid": sid, "ret": float("nan"), "truncated": False, "unbuyable": True})
            continue
        last = rs[-1]
        complete = last[1] == exit_ and last[2] is not None
        px = last[2] if complete else next((r[3] for r in reversed(rs) if r[3] is not None), None)
        if px is None:
            rows.append({"sid": sid, "ret": float("nan"), "truncated": True, "unbuyable": False})
            continue
        end_day = last[1]
        ratio = 1.0
        for ex, x in events.get(sid, []):
            if ex <= end_day:
                ratio *= x
        rows.append({"sid": sid, "ret": float(px) * ratio / float(first[2]) - 1, "truncated": not complete,
                     "unbuyable": False})
    return pd.DataFrame(rows)


def _t_stat(values: list[float]) -> float | None:
    if len(values) < 3:
        return None
    sd = float(np.std(values, ddof=1))
    return float(np.mean(values) / (sd / math.sqrt(len(values)))) if sd > 0 else None


def _p_two_sided(t: float | None) -> float | None:
    # normal approximation; dates are the sample units, so the study needs many of them to mean anything
    return None if t is None else math.erfc(abs(t) / math.sqrt(2))


def _summary(per_date: pd.DataFrame, quantiles: int) -> dict:
    if per_date.empty:
        return {"dates": 0}
    ics = per_date.ic.dropna().tolist()
    spread = per_date.spread.dropna().tolist()
    q_means = [float(per_date[f"q{i}"].mean()) for i in range(1, quantiles + 1)]
    t = _t_stat(ics)
    return {"dates": len(per_date), "mean_ic": float(np.mean(ics)) if ics else None, "ic_t": t,
            "ic_p_two_sided": _p_two_sided(t), "share_positive_ic": float(np.mean([x > 0 for x in ics])) if ics else None,
            "mean_quantile_return": q_means, "mean_top_minus_bottom": float(np.mean(spread)) if spread else None,
            "spread_t": _t_stat(spread),
            # rank correlation between quantile number and its mean return
            "monotonicity": float(pd.Series(q_means).rank().corr(pd.Series(range(quantiles), dtype=float)))
            if len(set(q_means)) > 1 else None,
            "mean_names_per_date": float(per_date.n.mean())}


def study_dates(session: Session, start: date, end: date, horizon: int, step: int | None) -> list[tuple]:
    """(decision T, entry T+1, exit T+1+h) on real trading days, non-overlapping by default."""
    step = step or horizon
    if step < horizon:
        raise ValueError("step < horizon would make the sample periods overlap")
    days = trading_days(session, start, end)
    out, i = [], 0
    while i + 1 + horizon < len(days):
        out.append((days[i], days[i + 1], days[i + 1 + horizon]))
        i += step
    return out


@dataclass
class StudyConfig:
    start: date
    end: date
    horizon: int = 20
    step: int | None = None
    quantiles: int = 5
    min_names: int = 50
    markets: tuple[str, ...] = ALL_MARKETS
    factors: tuple[str, ...] = (*FACTORS, "score_composite")
    oos_fraction: float = 0.3
    study_group: str = "technical_v1"
    universe_cache: dict = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict:
        return {"start": str(self.start), "end": str(self.end), "horizon": self.horizon,
                "step": self.step or self.horizon, "quantiles": self.quantiles, "min_names": self.min_names,
                "markets": list(self.markets), "factors": list(self.factors), "oos_fraction": self.oos_fraction,
                "study_group": self.study_group, "outcome": "total return, T+1 open to T+1+h open, before costs",
                "composite_members": list(COMPOSITE_SCORE)}


def run_study(session: Session, cfg: StudyConfig) -> dict:
    """Runs every factor of the config over the same dates and names. Returns {factor: result}."""
    dates = study_dates(session, cfg.start, cfg.end, cfg.horizon, cfg.step)
    regimes = load_regimes(session, cfg.end)
    rows: dict[str, list[dict]] = {f: [] for f in cfg.factors}
    counts = {"dates": 0, "names": 0, "unbuyable": 0, "truncated": 0, "skipped_dates": 0}
    for t, entry, exit_ in dates:
        view = DataView(session, t, markets=cfg.markets, universe_cache=cfg.universe_cache)
        eligible = view.universe().eligible
        table = factor_table(view, eligible)
        if table.empty or len(table) < cfg.min_names:
            counts["skipped_dates"] += 1
            continue
        fwd = forward_returns(session, table.sid.tolist(), entry, exit_)
        counts["dates"] += 1
        counts["names"] += len(fwd)
        counts["unbuyable"] += int(fwd.unbuyable.sum())
        counts["truncated"] += int(fwd.truncated.sum())
        merged = table.merge(fwd[["sid", "ret"]], on="sid").dropna(subset=["ret"])
        for f in cfg.factors:
            x = merged[[f, "ret"]].dropna()
            if len(x) < cfg.min_names:
                continue
            bins = pd.qcut(x[f].rank(method="first"), cfg.quantiles, labels=False) + 1
            means = x.groupby(bins)["ret"].mean()
            rows[f].append({"t": t, "n": len(x), "ic": float(x[f].rank().corr(x["ret"].rank())),
                            **{f"q{i}": float(means[i]) for i in range(1, cfg.quantiles + 1)},
                            "spread": float(means[cfg.quantiles] - means[1]), "regime": regimes.get(t, "UNKNOWN"),
                            "year": t.year})
    out = {}
    for f, rs in rows.items():
        df = pd.DataFrame(rs)
        res: dict = {"overall": _summary(df, cfg.quantiles), "sample_counts": counts}
        if not df.empty:
            cut = int(len(df) * (1 - cfg.oos_fraction))
            res["earlier_sample"] = _summary(df.iloc[:cut], cfg.quantiles)
            res["later_sample"] = _summary(df.iloc[cut:], cfg.quantiles)
            res["later_sample_start"] = str(df.t.iloc[cut]) if cut < len(df) else None
            res["by_year"] = {str(y): _summary(g, cfg.quantiles) for y, g in df.groupby("year")}
            res["by_regime"] = {k: _summary(g, cfg.quantiles) for k, g in df.groupby("regime")}
            res["per_date"] = [{"t": str(r["t"]), "ic": r["ic"], "spread": r["spread"], "n": r["n"]} for r in rs]
        out[f] = res
    return out


def multiple_testing(results: dict[str, dict]) -> dict:
    """Holm-adjusted p-values of the mean IC across one family of tests."""
    ps = sorted(((r["overall"].get("ic_p_two_sided"), k) for k, r in results.items()
                 if r["overall"].get("ic_p_two_sided") is not None), key=lambda x: (x[0], x[1]))
    m, running, out = len(ps), 0.0, {}
    for i, (p, k) in enumerate(ps):
        running = max(running, min(1.0, p * (m - i)))
        out[k] = {"raw_p": p, "holm_p": running}
    return {"family_size": m, "method": "Holm", "factors": out}


def evidence_label(result: dict, holm_p: float | None) -> str:
    """Descriptive only; there is deliberately no label meaning effective or tradable."""
    o, later = result["overall"], result.get("later_sample", {})
    if not o.get("dates") or o["dates"] < 24:
        return "INSUFFICIENT_SAMPLE"
    if holm_p is None or holm_p >= 0.05:
        return "NO_STATISTICAL_EVIDENCE"
    same_sign = (o.get("mean_ic") or 0) * (later.get("mean_ic") or 0) > 0
    return "STATISTICAL_ASSOCIATION_BEFORE_COSTS" if same_sign else "UNSTABLE_ACROSS_SAMPLES"


def save_studies(session: Session, cfg: StudyConfig, results: dict[str, dict], code_ref: str) -> list[int]:
    mt = multiple_testing(results)
    fp = data_fingerprint(session, cfg.start, cfg.end)
    ids = []
    for f, r in results.items():
        holm = mt["factors"].get(f, {}).get("holm_p")
        body = {**r, "holm_p": holm, "family_size": mt["family_size"], "evidence_label": evidence_label(r, holm),
                "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        row = FactorStudy(study_group=cfg.study_group, factor=f, horizon_days=cfg.horizon, period_start=cfg.start,
                          period_end=cfg.end, config=cfg.to_dict(), result=body, data_version_hash=fp["sha256"],
                          data_manifest=fp, code_ref=code_ref,
                          results_hash=hashlib.sha256(json.dumps(r, sort_keys=True, default=str).encode()).hexdigest(),
                          disclaimer=DISCLAIMER)
        session.add(row)
        session.flush()
        ids.append(row.id)
    return ids
