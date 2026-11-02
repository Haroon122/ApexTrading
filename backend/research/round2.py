"""Round 2: longer history (H1/H4/D1 from 2015, M15 from mid-2022) and more strategy families."""
import numpy as np
import pandas as pd
import engine
from engine import load, run, stats, atr
from strategies import donchian

pd.set_option("display.width", 250, "display.max_columns", 40)
H1, H4, D1, M15 = (load(f"xau_{t}_full.csv") for t in ("H1", "H4", "D1", "M15"))
SPLIT = "2021-01-01"


def rsi(close, n):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def keltner(df, n, k):
    mid = df["close"].ewm(span=n, adjust=False).mean()
    a = atr(df, n)
    up, lo = mid + k * a, mid - k * a
    c = df["close"]
    s = pd.Series(0, index=df.index)
    s[(c > up) & (c.shift() <= up.shift())] = 1
    s[(c < lo) & (c.shift() >= lo.shift())] = -1
    ex = pd.Series(0, index=df.index)
    ex[c < mid] = 1
    ex[c > mid] = -1
    return s, ex


def connors_rsi2(df, lo=10, hi=90, shorts=False):
    c = df["close"]
    r = rsi(c, 2)
    sma200, sma5 = c.rolling(200).mean(), c.rolling(5).mean()
    s = pd.Series(0, index=df.index)
    s[(r < lo) & (c > sma200)] = 1
    if shorts:
        s[(r > hi) & (c < sma200)] = -1
    ex = pd.Series(0, index=df.index)
    ex[c > sma5] = 1
    ex[c < sma5] = -1
    return s, ex


def with_daily_filter(df, s, sma=200):
    """Keep only signals in the direction of the daily close vs its SMA (no look-ahead: previous day)."""
    d = D1["close"]
    trend = np.sign(d - d.rolling(sma).mean()).shift(1)
    t = trend.reindex(df.index, method="ffill")
    return s.where(s == t, 0)


def evaluate(name, df, s, ex, **kw):
    t = run(df, s, exit_signal=ex, **kw)
    a, b = stats(t[t["open"] < SPLIT]), stats(t[t["open"] >= SPLIT])
    t["yr"] = t["open"].dt.year
    yrs = t.groupby("yr")["R"].sum()
    return {"strategy": name, "IS_n": a["n"], "IS_PF": a.get("PF"), "IS_R/yr": a.get("R/yr"), "IS_DD": a.get("maxDD_R"),
            "OOS_n": b["n"], "OOS_PF": b.get("PF"), "OOS_R/yr": b.get("R/yr"), "OOS_DD": b.get("maxDD_R"),
            "losing_yrs": int((yrs < 0).sum()), "yrs": len(yrs)}, t


rows, trades = [], {}
def add(name, df, s, ex, **kw):
    r, t = evaluate(name, df, s, ex, **kw)
    rows.append(r); trades[name] = t

# 1. Donchian H1 (current bot) on the longer history + neighbours
for n in (70, 100, 150):
    s, ex = donchian(H1, n)
    add(f"Donchian H1 N{n}", H1, s, ex, stop_atr=3, trail_atr=4)
# 2. Donchian H1 + daily 200-SMA trend filter
s, ex = donchian(H1, 100)
add("Donchian H1 N100 + D1 trend filter", H1, with_daily_filter(H1, s), ex, stop_atr=3, trail_atr=4)
# 3. Donchian H4
for n in (55, 100):
    s, ex = donchian(H4, n)
    add(f"Donchian H4 N{n}", H4, s, ex, stop_atr=3, trail_atr=4)
# 4. Turtle (D1): 20/55-day breakout, 2 ATR stop, exit on 10/20-day opposite channel
for n in (20, 55):
    s, ex = donchian(D1, n)
    add(f"Turtle D1 {n}-day", D1, s, ex, stop_atr=2, trail_atr=None)
# 5. Keltner breakout H1
for n, k in ((50, 2.0), (100, 2.5)):
    s, ex = keltner(H1, n, k)
    add(f"Keltner H1 EMA{n} x{k}ATR", H1, s, ex, stop_atr=3, trail_atr=4)
# 6. Connors RSI(2) mean reversion (D1 and H4), long-only and long/short
for df, tf in ((D1, "D1"), (H4, "H4")):
    for sh in (False, True):
        s, ex = connors_rsi2(df, shorts=sh)
        add(f"RSI2 meanrev {tf} {'L/S' if sh else 'long'}", df, s, ex, stop_atr=3, trail_atr=None, max_bars=10)

out = pd.DataFrame(rows)
print(out.to_string(index=False))

# M15 (shorter history: mid-2022 onward; split at 2024-07)
print("\n=== M15 (Jul 2022 - Oct 2026), split 2024-07-01 ===")
for n in (100, 200, 400, 600):
    s, ex = donchian(M15, n)
    t = run(M15, s, exit_signal=ex, stop_atr=3, trail_atr=4)
    a, b = stats(t[t["open"] < "2024-07-01"]), stats(t[t["open"] >= "2024-07-01"])
    print(f"Donchian M15 N{n}: IS PF {a.get('PF')} R/yr {a.get('R/yr')} DD {a.get('maxDD_R')} | OOS PF {b.get('PF')} R/yr {b.get('R/yr')} DD {b.get('maxDD_R')}")

pd.to_pickle(trades, "round2_trades.pkl")
