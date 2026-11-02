import sys
import numpy as np
import pandas as pd
from engine import load, run, stats, atr

H1 = load("xau_H1.csv")
SPLIT = "2023-01-01"


def cross_up(x, y):
    return (x > y) & (x.shift() <= y.shift())


def donchian(df, n, ema=None, longs=True, shorts=True):
    hi = df["high"].rolling(n).max().shift()
    lo = df["low"].rolling(n).min().shift()
    up = (df["close"] > hi) & (df["close"].shift() <= hi.shift())
    dn = (df["close"] < lo) & (df["close"].shift() >= lo.shift())
    if ema:
        e = df["close"].ewm(span=ema, adjust=False).mean()
        up &= df["close"] > e
        dn &= df["close"] < e
    s = pd.Series(0, index=df.index)
    if longs: s[up] = 1
    if shorts: s[dn] = -1
    # exit when opposite half-length channel breaks
    m = max(n // 2, 5)
    ex = pd.Series(0, index=df.index)
    ex[df["close"] < df["low"].rolling(m).min().shift()] = 1
    ex[df["close"] > df["high"].rolling(m).max().shift()] = -1
    return s, ex


def tsmom(df, lb, longs=True, shorts=True):
    mom = np.sign(df["close"] - df["close"].shift(lb))
    flip = mom != mom.shift()
    s = pd.Series(0, index=df.index)
    if longs: s[flip & (mom > 0)] = 1
    if shorts: s[flip & (mom < 0)] = -1
    ex = pd.Series(0, index=df.index)
    ex[mom < 0] = 1
    ex[mom > 0] = -1
    return s, ex


def ema_cross(df, f, sl, longs=True, shorts=True):
    a = df["close"].ewm(span=f, adjust=False).mean()
    b = df["close"].ewm(span=sl, adjust=False).mean()
    s = pd.Series(0, index=df.index)
    if longs: s[cross_up(a, b)] = 1
    if shorts: s[cross_up(b, a)] = -1
    ex = pd.Series(0, index=df.index)
    ex[a < b] = 1
    ex[a > b] = -1
    return s, ex


def london_breakout(df, r0=1, r1=9, t0=9, t1=17):
    """Asian range = server hours [r0, r1); trade first breakout in [t0, t1)."""
    d = df.copy()
    d["day"] = d.index.normalize()
    hr = d.index.hour
    rng = d[(hr >= r0) & (hr < r1)].groupby("day").agg(rh=("high", "max"), rl=("low", "min"))
    d = d.join(rng, on="day")
    win = (hr >= t0) & (hr < t1)
    up = win & (d["close"] > d["rh"])
    dn = win & (d["close"] < d["rl"])
    first_up = up & (up.groupby(d["day"]).cumsum() == 1)
    first_dn = dn & (dn.groupby(d["day"]).cumsum() == 1)
    s = pd.Series(0, index=df.index)
    s[first_up] = 1
    s[first_dn] = -1
    return s, None


def evaluate(name, df, sig, ex, **kw):
    t = run(df, sig, exit_signal=ex, **kw)
    if len(t) == 0:
        return None
    a = stats(t[t["open"] < SPLIT], "IS")
    b = stats(t[t["open"] >= SPLIT], "OOS")
    return name, a, b, t


if __name__ == "__main__":
    rows = []
    def add(res):
        if res:
            name, a, b, _ = res
            rows.append({"strategy": name, **{f"IS_{k}": v for k, v in a.items() if k != "label"},
                         **{f"OOS_{k}": v for k, v in b.items() if k != "label"}})

    for n in (20, 55, 100, 200):
        for ema in (None, 200):
            for tr in (3.0, 4.0):
                s, ex = donchian(H1, n, ema)
                add(evaluate(f"Donchian{n} ema{ema} trail{tr}", H1, s, ex, stop_atr=2.0, trail_atr=tr))
    for lb in (120, 240, 480):
        for tr in (3.0, 4.0):
            s, ex = tsmom(H1, lb)
            add(evaluate(f"TSMOM{lb} trail{tr}", H1, s, ex, stop_atr=2.0, trail_atr=tr))
    for f, sl in ((20, 100), (50, 200)):
        s, ex = ema_cross(H1, f, sl)
        add(evaluate(f"EMA{f}/{sl}", H1, s, ex, stop_atr=2.0, trail_atr=3.0))
    for (t0, t1) in ((9, 13), (9, 17)):
        for tp in (1.5, 2.0, None):
            s, _ = london_breakout(H1, 1, 9, t0, t1)
            add(evaluate(f"LondonBO {t0}-{t1} tp{tp}", H1, s, None, stop_atr=1.0,
                         trail_atr=None if tp else 1.5, tp_r=tp, eod_hour=22))

    out = pd.DataFrame(rows)
    pd.set_option("display.width", 250, "display.max_columns", 40)
    cols = ["strategy", "IS_n", "IS_win%", "IS_PF", "IS_R/yr", "IS_maxDD_R", "OOS_n", "OOS_win%", "OOS_PF", "OOS_R/yr", "OOS_maxDD_R"]
    print(out[cols].sort_values("IS_PF", ascending=False).to_string(index=False))
    out.to_csv("results_round1.csv", index=False)
