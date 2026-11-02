import pandas as pd
from engine import run, stats
from strategies import H1, SPLIT, donchian

pd.set_option("display.width", 250, "display.max_columns", 40)


def row(name, t):
    a, b = stats(t[t["open"] < SPLIT]), stats(t[t["open"] >= SPLIT])
    return {"cfg": name, "IS_n": a["n"], "IS_PF": a.get("PF"), "IS_R/yr": a.get("R/yr"), "IS_DD": a.get("maxDD_R"),
            "OOS_n": b["n"], "OOS_PF": b.get("PF"), "OOS_R/yr": b.get("R/yr"), "OOS_DD": b.get("maxDD_R")}


print("=== 1. Neighbourhood grid (Donchian N x stop x trail) ===")
rows = []
for n in (70, 85, 100, 120, 150):
    s, ex = donchian(H1, n)
    for st in (1.5, 2.0, 3.0):
        for tr in (2.5, 3.0, 4.0, 5.0):
            rows.append(row(f"N{n} st{st} tr{tr}", run(H1, s, exit_signal=ex, stop_atr=st, trail_atr=tr)))
g = pd.DataFrame(rows)
print(g.to_string(index=False))
print("share of configs with PF>1 in BOTH periods:", round(((g.IS_PF > 1) & (g.OOS_PF > 1)).mean() * 100, 1), "%")

print("\n=== 2. Long vs short (N100 st2 tr3) ===")
for lg, sh, nm in ((True, True, "both"), (True, False, "long only"), (False, True, "short only")):
    s, ex = donchian(H1, 100, longs=lg, shorts=sh)
    print(row(nm, run(H1, s, exit_signal=ex, stop_atr=2.0, trail_atr=3.0)))

print("\n=== 3. Management variants (N100 st2 tr3) ===")
s, ex = donchian(H1, 100)
variants = {
    "base": {},
    "no exit signal": {"_noex": True},
    "partial 50% @1R": {"partial_r": 1.0},
    "BE @1R": {"be_r": 1.0},
    "pyramid +1 @1R": {"pyramid": 1, "add_min_r": 1.0},
    "pyramid +2 @1R": {"pyramid": 2, "add_min_r": 1.0},
    "pyramid +2 @1.5R": {"pyramid": 2, "add_min_r": 1.5},
}
for nm, kw in variants.items():
    e = None if kw.pop("_noex", False) else ex
    print(row(nm, run(H1, s, exit_signal=e, stop_atr=2.0, trail_atr=3.0, **kw)))

print("\n=== 4. Year by year (N100 st2 tr3, base) ===")
t = run(H1, s, exit_signal=ex, stop_atr=2.0, trail_atr=3.0)
t["yr"] = t["open"].dt.year
print(t.groupby("yr")["R"].agg(["count", "sum", lambda r: round((r > 0).mean() * 100, 1)]).round(1).to_string())
print("exit reasons:", t["why"].value_counts().to_dict(), "avg bars held:", round(t["bars"].mean(), 1))
t.to_csv("trades_d100.csv", index=False)
