"""
Simple, conservative bar-by-bar backtester for XAUUSD research.

- Signals are computed on a CLOSED bar, entry at the NEXT bar open.
- Costs: real spread from MT5 data (points) + slippage, swap per night held.
- If stop and target are both touched inside one bar, the stop is assumed first.
- Results in R (1R = initial stop distance), so they don't depend on lot size.
"""
import numpy as np
import pandas as pd

POINT = 0.01
SLIPPAGE = 0.10          # $ per side
SWAP_LONG = -12.6 * POINT  # price units per night (from symbol_info)
SWAP_SHORT = -4.6 * POINT


def load(path):
    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("time")
    df["spread_px"] = df["spread"].clip(lower=10) * POINT
    return df


def atr(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def run(df, signal, stop_atr=2.0, trail_atr=3.0, tp_r=None, partial_r=None,
        be_r=None, max_bars=None, eod_hour=None, pyramid=0, add_min_r=1.0,
        exit_signal=None):
    """
    signal: Series of +1/-1/0 decided at bar close.
    exit_signal: optional Series, +1 means close longs, -1 close shorts.
    pyramid: extra positions allowed while the first is >= add_min_r and its SL is at breakeven.
    """
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    a = atr(df).values
    sp = df["spread_px"].values
    sig = signal.reindex(df.index).fillna(0).values
    ex = exit_signal.reindex(df.index).fillna(0).values if exit_signal is not None else np.zeros(len(df))
    hours = df.index.hour.values
    days = df.index.normalize().values
    times = df.index

    trades, open_pos = [], []
    for i in range(1, len(df) - 1):
        # ---- manage open positions on bar i ----
        still = []
        for p in open_pos:
            d = p["dir"]
            closed, exit_px, why = False, None, ""
            # stop
            if (d == 1 and l[i] <= p["sl"]) or (d == -1 and h[i] + sp[i] >= p["sl"]):
                exit_px = p["sl"] if not ((d == 1 and o[i] < p["sl"]) or (d == -1 and o[i] > p["sl"])) else o[i]
                closed, why = True, "SL"
            # partial
            if not closed and partial_r and not p["partial_done"]:
                tgt = p["entry"] + d * partial_r * p["risk"]
                if (d == 1 and h[i] >= tgt) or (d == -1 and l[i] + sp[i] <= tgt):
                    p["realized"] += 0.5 * partial_r
                    p["size"] = 0.5
                    p["partial_done"] = True
                    p["sl"] = p["entry"] if d == 1 else p["entry"]
            # take profit
            if not closed and tp_r:
                tgt = p["entry"] + d * tp_r * p["risk"]
                if (d == 1 and h[i] >= tgt) or (d == -1 and l[i] + sp[i] <= tgt):
                    exit_px, closed, why = tgt, True, "TP"
            # breakeven
            if not closed and be_r and not p["be"]:
                if (c[i] - p["entry"]) * d >= be_r * p["risk"]:
                    p["sl"] = p["entry"] + d * 0.1 * p["risk"]
                    p["be"] = True
            # trailing (chandelier on closes)
            if not closed and trail_atr:
                if d == 1:
                    p["sl"] = max(p["sl"], c[i] - trail_atr * a[i])
                else:
                    p["sl"] = min(p["sl"], c[i] + trail_atr * a[i])
                if (p["sl"] - p["entry"]) * d >= 0:
                    p["be"] = True
            # signal exit / time / eod at close
            if not closed and ex[i] == d:
                exit_px, closed, why = c[i], True, "EXIT_SIG"
            if not closed and max_bars and i - p["bar"] >= max_bars:
                exit_px, closed, why = c[i], True, "TIME"
            if not closed and eod_hour is not None and hours[i] >= eod_hour:
                exit_px, closed, why = c[i], True, "EOD"

            if closed:
                if d == -1:
                    exit_px += sp[i]  # buy back at ask
                exit_px -= d * SLIPPAGE
                nights = int((days[i] - p["day"]) / np.timedelta64(1, "D"))
                swap = nights * (SWAP_LONG if d == 1 else SWAP_SHORT)
                r = (exit_px - p["entry"]) * d / p["risk"] + swap / p["risk"]
                p["realized"] += p["size"] * r
                trades.append({"open": p["t"], "close": times[i], "dir": d, "R": p["realized"],
                               "why": why, "layer": p["layer"], "bars": i - p["bar"]})
            else:
                still.append(p)
        open_pos = still

        # ---- new entry decided at close of bar i, filled at open of i+1 ----
        s = sig[i]
        if s == 0 or a[i] <= 0 or np.isnan(a[i]):
            continue
        same = [p for p in open_pos if p["dir"] == s]
        if any(p["dir"] == -s for p in open_pos):
            continue
        if same:
            if len(same) > pyramid:
                continue
            first = same[0]
            if not first["be"] or (c[i] - first["entry"]) * s < add_min_r * first["risk"]:
                continue
        entry = o[i + 1] + (sp[i + 1] if s == 1 else 0) + s * SLIPPAGE
        risk = stop_atr * a[i]
        open_pos.append({"dir": s, "entry": entry, "risk": risk, "sl": entry - s * risk,
                         "t": times[i + 1], "bar": i + 1, "day": days[i + 1], "be": False,
                         "partial_done": False, "size": 1.0, "realized": 0.0, "layer": len(same)})
    return pd.DataFrame(trades)


def stats(t, label=""):
    if t is None or len(t) == 0:
        return {"label": label, "n": 0}
    R = t["R"]
    wins, losses = R[R > 0].sum(), -R[R < 0].sum()
    eq = R.cumsum()
    dd = (eq.cummax() - eq).max()
    yrs = max((t["close"].max() - t["open"].min()).days / 365.25, 0.1)
    return {"label": label, "n": len(t), "trades/yr": round(len(t) / yrs, 1),
            "win%": round((R > 0).mean() * 100, 1), "PF": round(wins / losses, 2) if losses else np.inf,
            "totR": round(R.sum(), 1), "R/yr": round(R.sum() / yrs, 1), "maxDD_R": round(dd, 1),
            "avgW": round(R[R > 0].mean(), 2) if (R > 0).any() else 0,
            "avgL": round(R[R < 0].mean(), 2) if (R < 0).any() else 0}
