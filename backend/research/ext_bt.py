"""Donchian H1 (85-bar, daily SMA100 trend): does an 'already extended' breakout do worse, and do partial profits help?"""
import numpy as np, pandas as pd
from engine import load, run, stats, atr
pd.set_option("display.width", 250, "display.max_columns", 30)
H1 = load("xau_H1.csv"); SPLIT = "2023-01-01"
d = H1.close.resample("1D").last().dropna()
up = (d > d.rolling(100).mean()).shift(1)
def sig(df, n=85):
    hi = df.high.rolling(n).max().shift(); lo = df.low.rolling(n).min().shift()
    u_ = (df.close > hi) & (df.close.shift() <= hi.shift()); d_ = (df.close < lo) & (df.close.shift() >= lo.shift())
    day = df.index.normalize(); u = pd.Series(up.reindex(day).values, index=df.index)
    have = u.notna(); u = u.fillna(False).astype(bool)
    s = pd.Series(0, index=df.index); s[u_ & u & have] = 1; s[d_ & (~u) & have] = -1
    return s
a = atr(H1)
s = sig(H1)
# features at the signal bar
ext24 = pd.Series(0.0, index=H1.index)      # how far price already moved in the trade direction in last 24 bars (ATR)
mv = np.where(s == -1, (H1.high.rolling(24).max() - H1.close) / a, (H1.close - H1.low.rolling(24).min()) / a)
ext24 = pd.Series(mv, index=H1.index)
e50 = H1.close.ewm(span=50, adjust=False).mean()
dist50 = ((H1.close - e50).abs() / a)
base = dict(stop_atr=3.0, trail_atr=4.0)
t = run(H1, s, **base)
idx = H1.index.get_indexer(t.open) - 1
t["ext24"] = ext24.values[idx]; t["dist50"] = dist50.values[idx]
def row(lbl, x):
    al, a_, b_ = stats(x), stats(x[x.open < SPLIT]), stats(x[x.open >= SPLIT])
    return dict(case=lbl, n=al["n"], win=al["win%"], PF=al["PF"], totR=al["totR"], maxDD=al["maxDD_R"], IS_PF=a_.get("PF"), OOS_PF=b_.get("PF"))
rows = [row("baseline", t)]
for col in ("ext24", "dist50"):
    qs = t[col].quantile([0, .33, .66, 1]).values
    for k in range(3):
        sub = t[(t[col] >= qs[k]) & (t[col] <= qs[k + 1])]
        rows.append(row(f"{col} bucket{k+1} [{qs[k]:.1f}-{qs[k+1]:.1f}]", sub))
for thr in (3, 4, 5, 6):
    rows.append(row(f"skip if ext24>{thr}", t[t.ext24 <= thr]))
for thr in (2.5, 3.5, 4.5):
    rows.append(row(f"skip if dist50>{thr}", t[t.dist50 <= thr]))
for pr in (1.0, 1.5, 2.0, 3.0):
    rows.append(row(f"partial 50% at {pr}R + BE", run(H1, s, partial_r=pr, **base)))
rows.append(row("be_r=1.5", run(H1, s, be_r=1.5, **base)))
print(pd.DataFrame(rows).to_string(index=False))
