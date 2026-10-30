"""Technical indicators implemented with pandas/numpy and verified against reference values
and independent loop implementations in tests/test_indicators.py.

Conventions (documented because Taiwan brokers differ):
* KD: 9-day RSV, K = 2/3·K[-1] + 1/3·RSV, D = 2/3·D[-1] + 1/3·K, seeded at 50 (台灣常用).
* RSI: Wilder smoothing, seeded with the simple mean of the first n changes.
* MACD: EMA(12) − EMA(26) of close, signal EMA(9) of DIF; hist = DIF − signal.
* Bollinger: SMA(20) ± 2·population std (ddof=0), as defined by Bollinger.
* ATR: Wilder-smoothed true range.
All functions are causal: the value at t uses data up to and including t only.
"""
import numpy as np
import pandas as pd

CALC_VERSION = "1.0"


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def _wilder(values: np.ndarray, n: int) -> np.ndarray:
    """Wilder smoothing seeded by the mean of the first n non-NaN values."""
    out = np.full(len(values), np.nan)
    valid = np.where(~np.isnan(values))[0]
    if len(valid) < n:
        return out
    start = valid[0]
    seed_end = start + n
    if np.isnan(values[start:seed_end]).any():
        return out
    out[seed_end - 1] = values[start:seed_end].mean()
    for i in range(seed_end, len(values)):
        v = values[i]
        out[i] = out[i - 1] if np.isnan(v) else (out[i - 1] * (n - 1) + v) / n
    return out


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    diff = close.diff().to_numpy(dtype=float)
    gain = np.where(np.isnan(diff), np.nan, np.clip(diff, 0, None))
    loss = np.where(np.isnan(diff), np.nan, np.clip(-diff, 0, None))
    ag, al = _wilder(gain, n), _wilder(loss, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = ag / al
        out = 100 - 100 / (1 + rs)
    out = np.where((al == 0) & (ag > 0), 100.0, out)
    out = np.where((al == 0) & (ag == 0), 50.0, out)
    return pd.Series(out, index=close.index)


def kd(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 9) -> tuple[pd.Series, pd.Series]:
    ln, hn = low.rolling(n, min_periods=n).min(), high.rolling(n, min_periods=n).max()
    rng = (hn - ln).to_numpy(dtype=float)
    raw = (close - ln).to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        rsv = np.where(rng > 0, raw / rng * 100, 50.0)
    rsv = np.where(np.isnan(ln.to_numpy(dtype=float)), np.nan, rsv)
    k = np.full(len(close), np.nan)
    d = np.full(len(close), np.nan)
    pk = pd_ = 50.0
    for i, r in enumerate(rsv):
        if np.isnan(r):
            continue
        pk = 2 / 3 * pk + r / 3
        pd_ = 2 / 3 * pd_ + pk / 3
        k[i], d[i] = pk, pd_
    return pd.Series(k, index=close.index), pd.Series(d, index=close.index)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    dif = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    dif = dif.where(close.expanding().count() >= slow)
    sig = dif.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return dif, sig, dif - sig


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0):
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    return mid + k * sd, mid, mid - k * sd


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    pc = close.shift(1)
    tr = pd.concat([high - low, (high - pc).abs(), (low - pc).abs()], axis=1).max(axis=1, skipna=False)
    tr.iloc[0] = high.iloc[0] - low.iloc[0]
    return pd.Series(_wilder(tr.to_numpy(dtype=float), n), index=close.index)


def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    """df: one security, sorted by date, columns open/high/low/close/volume/turnover (adjusted prices)."""
    c, h, l, v = df["close"].astype(float), df["high"].astype(float), df["low"].astype(float), df["volume"].astype(float)
    out = pd.DataFrame(index=df.index)
    for n in (5, 10, 20, 60):
        out[f"ma{n}"] = sma(c, n)
    out["k9"], out["d9"] = kd(h, l, c, 9)
    out["rsi6"], out["rsi14"] = rsi(c, 6), rsi(c, 14)
    out["macd_dif"], out["macd_signal"], out["macd_hist"] = macd(c)
    out["bb_upper"], out["bb_mid"], out["bb_lower"] = bollinger(c)
    out["vol_ma5"], out["vol_ma20"] = sma(v, 5), sma(v, 20)
    out["turnover_ma20"] = sma(df["turnover"].astype(float), 20)
    out["atr14"] = atr(h, l, c, 14)
    out["high_20"], out["low_20"] = h.rolling(20, min_periods=20).max(), l.rolling(20, min_periods=20).min()
    out["high_60"] = h.rolling(60, min_periods=60).max()
    prior = v.shift(1).rolling(20, min_periods=20).mean()
    out["volume_ratio"] = v / prior.replace(0, np.nan)
    return out
