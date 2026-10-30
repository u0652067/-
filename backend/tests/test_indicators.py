import numpy as np
import pandas as pd
import pytest

from aqt.indicators import technical as ta

# Wilder RSI reference series (StockCharts "RSI" ChartSchool worked example). StockCharts rounds the
# intermediate averages to 2 dp, so its published values sit ~0.07 above the exact ones (exact values
# re-derived by an independent loop: 70.464, 66.250, ...). We assert exact values tightly, the article loosely.
RSI_CLOSES = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28,
              46.28, 46.00, 46.03, 46.41, 46.22, 45.64]
RSI_PUBLISHED = {14: 70.53, 15: 66.32, 16: 66.55, 17: 69.41, 18: 66.36, 19: 57.97}
RSI_EXACT = {14: 70.464, 15: 66.250, 16: 66.481, 17: 69.347, 18: 66.295, 19: 57.915}


def test_rsi_matches_reference():
    r = ta.rsi(pd.Series(RSI_CLOSES), 14)
    assert r.iloc[:14].isna().all()
    for i, v in RSI_EXACT.items():
        assert r.iloc[i] == pytest.approx(v, abs=0.001), i
        assert r.iloc[i] == pytest.approx(RSI_PUBLISHED[i], abs=0.1), i


def test_rsi_edge_cases():
    up = ta.rsi(pd.Series(np.arange(1, 30, dtype=float)), 14)
    assert up.iloc[-1] == 100
    flat = ta.rsi(pd.Series([10.0] * 30), 14)
    assert flat.iloc[-1] == 50


def _series(n=200, seed=1):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    h = c * (1 + np.abs(rng.normal(0, 0.01, n)))
    l = c * (1 - np.abs(rng.normal(0, 0.01, n)))
    o = (h + l) / 2
    v = rng.integers(1_000, 100_000, n).astype(float)
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v, "turnover": v * c})


def test_sma_and_bollinger_vs_numpy():
    df = _series()
    up, mid, lo = ta.bollinger(df.close, 20, 2)
    i = 150
    w = df.close.iloc[i - 19:i + 1].to_numpy()
    assert mid.iloc[i] == pytest.approx(w.mean())
    assert up.iloc[i] == pytest.approx(w.mean() + 2 * w.std(ddof=0))
    assert lo.iloc[i] == pytest.approx(w.mean() - 2 * w.std(ddof=0))
    assert ta.sma(df.close, 5).iloc[3] != ta.sma(df.close, 5).iloc[3]  # NaN before window full


def test_kd_vs_loop():
    df = _series()
    k, d = ta.kd(df.high, df.low, df.close, 9)
    pk = pd_ = 50.0
    for i in range(len(df)):
        if i < 8:
            assert np.isnan(k.iloc[i])
            continue
        hh, ll = df.high.iloc[i - 8:i + 1].max(), df.low.iloc[i - 8:i + 1].min()
        rsv = (df.close.iloc[i] - ll) / (hh - ll) * 100
        pk = pk * 2 / 3 + rsv / 3
        pd_ = pd_ * 2 / 3 + pk / 3
        assert k.iloc[i] == pytest.approx(pk) and d.iloc[i] == pytest.approx(pd_)
    assert ((k.dropna() >= 0) & (k.dropna() <= 100)).all()


def test_macd_vs_loop():
    df = _series()
    dif, sig, hist = ta.macd(df.close)
    a12, a26 = 2 / 13, 2 / 27
    e12 = e26 = df.close.iloc[0]
    difs = []
    for c in df.close:
        e12 = e12 + a12 * (c - e12)
        e26 = e26 + a26 * (c - e26)
        difs.append(e12 - e26)
    assert dif.iloc[:25].isna().all()
    assert dif.iloc[100] == pytest.approx(difs[100])
    assert hist.iloc[150] == pytest.approx(dif.iloc[150] - sig.iloc[150])


def test_atr_positive_and_wilder():
    df = _series()
    a = ta.atr(df.high, df.low, df.close, 14)
    assert a.iloc[:13].isna().all() and (a.dropna() > 0).all()


def test_indicators_are_causal():
    """Value at t must not change when future bars are appended (no look-ahead)."""
    df = _series(220)
    full = ta.compute_all(df)
    part = ta.compute_all(df.iloc[:150])
    pd.testing.assert_frame_equal(full.iloc[:150], part, check_exact=False, rtol=1e-9)


def test_volume_ratio_excludes_today():
    df = _series(40)
    df.loc[30, "volume"] = df.volume.iloc[10:30].mean() * 5
    out = ta.compute_all(df)
    assert out.volume_ratio.iloc[30] == pytest.approx(5.0)
