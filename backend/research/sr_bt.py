"""Backtest of automatic support/resistance (uses the SAME live code: app.services.support_resistance).

No look-ahead: the levels in force at bar i were computed from closed bars <= the last checkpoint <= i.
Strategies: bounce (reject a level), break (close through a level), each with/without the daily trend filter,
plus a confluence test on the existing Pullback signals.
"""
import sys, types
sys.path.insert(0, "..")
import numpy as np, pandas as pd
from engine import load, run, stats, atr
from pullback_bt import daily_trend, pullback
from app.services import support_resistance as sr
pd.set_option("display.width", 250, "display.max_columns", 30)

WINDOW, STEP = 500, {"H1": 6, "H4": 4, "M15": 8}


def checkpoints(df, tf):
    """levels[i] = list of (price, touches) valid for bar i (computed from bars <= checkpoint)."""
    ts = df.index.to_pydatetime()
    bars = [types.SimpleNamespace(timestamp=ts[i], open=o, high=h, low=l, close=c, volume=1, is_closed=True)
            for i, (o, h, l, c) in enumerate(zip(df.open, df.high, df.low, df.close))]
    step, out, cur = STEP[tf], [None] * len(df), []
    for i in range(len(df)):
        if i >= 60 and i % step == 0:
            lv = sr.levels_for(bars[max(0, i - WINDOW + 1):i + 1], tf)
            cur = [(x["price"], x["touches"]) for x in lv]
        out[i] = cur
    return out


def signals(df, levels, mode, min_touch, up_day=None):
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    a = atr(df).values
    sig = np.zeros(len(df))
    trend = None
    if up_day is not None:
        u = up_day.reindex(df.index.normalize()).values
        trend = np.where(pd.isna(u), 0, np.where(u.astype(float) > 0, 1, -1))
    for i in range(61, len(df)):
        if not levels[i] or a[i] <= 0:
            continue
        for p, t in levels[i]:
            if t < min_touch:
                continue
            tol = a[i]
            if mode.startswith("bounce"):
                if l[i] <= p + 0.15 * tol and l[i] >= p - 0.6 * tol and c[i] > p and c[i] > o[i] and c[i - 1] > p:
                    sig[i] = 1
                elif h[i] >= p - 0.15 * tol and h[i] <= p + 0.6 * tol and c[i] < p and c[i] < o[i] and c[i - 1] < p:
                    sig[i] = -1
            else:
                if c[i] > p + 0.2 * tol and c[i - 1] <= p and c[i] > o[i]:
                    sig[i] = 1
                elif c[i] < p - 0.2 * tol and c[i - 1] >= p and c[i] < o[i]:
                    sig[i] = -1
            if sig[i]:
                break
        if sig[i] and mode.endswith("trend") and trend is not None and trend[i] != sig[i]:
            sig[i] = 0
    return pd.Series(sig, index=df.index)


def confluence(df, levels, base, tol_atr=0.6, need=True):
    """Keep pullback signals that sit next to a level in the trade direction (support for longs)."""
    c = df.close.values; l = df.low.values; h = df.high.values; a = atr(df).values
    keep = np.zeros(len(df))
    for i in np.flatnonzero(base.values != 0):
        s, near = base.values[i], False
        for p, t in (levels[i] or []):
            if s == 1 and abs(l[i] - p) <= tol_atr * a[i] and p <= c[i]: near = True
            if s == -1 and abs(h[i] - p) <= tol_atr * a[i] and p >= c[i]: near = True
        keep[i] = s if near == need else 0
    return pd.Series(keep, index=df.index)


EXITS = {"tp2": dict(stop_atr=1.5, trail_atr=None, tp_r=2.0, max_bars=80),
         "tp3": dict(stop_atr=1.5, trail_atr=None, tp_r=3.0, max_bars=120),
         "trail": dict(stop_atr=2.0, trail_atr=3.0)}

if __name__ == "__main__":
    h1 = load("xau_H1.csv"); ut = daily_trend(h1)
    cfg = {"H1": ("xau_H1.csv", "2023-01-01"), "H4": ("xau_H4.csv", "2023-01-01"), "M15": ("xau_M15.csv", "2025-01-01")}
    rows, pb_rows = [], []
    for tf, (f, split) in cfg.items():
        df = load(f); lv = checkpoints(df, tf)
        print(tf, "levels ready", flush=True)
        for mode in ("bounce", "bounce_trend", "break", "break_trend"):
            for mt in (2, 4):
                s = signals(df, lv, mode, mt, ut)
                for en, kw in EXITS.items():
                    t = run(df, s, **kw)
                    if len(t) < 5: continue
                    al, a_, b_ = stats(t), stats(t[t.open < split]), stats(t[t.open >= split])
                    rows.append(dict(tf=tf, mode=mode, touches=mt, exit=en, n=al["n"], tr_yr=al["trades/yr"], win=al["win%"],
                                     PF=al["PF"], totR=al["totR"], maxDD=al["maxDD_R"], IS_PF=a_.get("PF"), OOS_PF=b_.get("PF")))
        base = pullback(df, ut, 20)
        for name, s in (("pullback_all", base), ("pullback_at_level", confluence(df, lv, base, need=True)),
                        ("pullback_no_level", confluence(df, lv, base, need=False))):
            t = run(df, s, **(dict(stop_atr=2.0, trail_atr=3.0) if tf == "H4" else dict(stop_atr=3.0, trail_atr=4.0)))
            if len(t) < 5: continue
            al, a_, b_ = stats(t), stats(t[t.open < split]), stats(t[t.open >= split])
            pb_rows.append(dict(tf=tf, variant=name, n=al["n"], tr_yr=al["trades/yr"], win=al["win%"], PF=al["PF"], totR=al["totR"],
                                maxDD=al["maxDD_R"], IS_PF=a_.get("PF"), OOS_PF=b_.get("PF")))
    o = pd.DataFrame(rows); o.to_csv("results_sr.csv", index=False)
    p = pd.DataFrame(pb_rows); p.to_csv("results_sr_pullback.csv", index=False)
    print(o.sort_values("PF", ascending=False).to_string(index=False))
    print(o.groupby(["tf", "mode"]).agg(best=("PF", "max"), median=("PF", "median"), profitable=("PF", lambda x: (x > 1).sum()), total=("PF", "count")))
    print(p.to_string(index=False))
