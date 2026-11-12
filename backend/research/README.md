# Strategy research (XAUUSD)

Backtests behind the `DONCHIAN_TREND` strategy profile (`app/services/donchian_trend.py`).

## Re-run
1. Export H1 history from MT5 into this folder as `xau_H1.csv`:
   ```python
   import MetaTrader5 as mt5, pandas as pd
   mt5.initialize(); mt5.symbol_select("XAUUSD", True)
   pd.DataFrame(mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_H1, 0, 50000)).to_csv("xau_H1.csv", index=False)
   ```
2. `python strategies.py` (compares strategies), then `python robust.py` (parameter grid, long/short, pyramiding, year-by-year).

## How the test works
- Signal on a closed bar, filled at the next bar's open.
- Real MT5 spread per bar, $0.10 slippage per side, and swap for each night held.
- If the stop and the target are hit in the same bar, the stop counts first.
- Settings are picked on 2018-2022 data, then checked unchanged on 2023-2026.

## Results (MetaQuotes-Demo data, May 2018 to Oct 2026)
| Strategy | 2018-22 PF | 2023-26 PF |
|---|---|---|
| **Donchian 100 H1, stop 3 ATR, trail 4 ATR (chosen)** | **1.21** | **1.40** |
| Donchian 100, stop 2 / trail 3 | 1.15 | 1.22 |
| London session breakout (several variants) | 0.80-0.94 | 0.85-0.98 |
| Time-series momentum 120/240 | 0.82-0.95 | 0.83-0.91 |

- 92% of Donchian settings (N 70-150, stop 1.5-3, trail 2.5-5) had PF > 1 in both periods.
- With 2x costs: PF 1.16 / 1.37. With 3x costs: PF 1.07 / 1.33.
- About 60 trades a year, win rate 34-40%, longest losing streak 15 trades.
- Monte Carlo drawdown: median 20R, 95th percentile 37R.
- Losing years: 2018, 2021, 2022.
- Pyramiding and partial closes did not improve the risk-adjusted result.

## Round 2 (`round2.py`): longer history and more strategies
Data: H1/H4/D1 from Jan 2015, M15 from Jul 2022 (MT5 "Max bars" = 100,000 limits M15).
Export files as `xau_<TF>_full.csv` (yearly `copy_rates_range` calls).
Split: settings judged on 2015-2020, checked unchanged on 2021-2026.

| Strategy | 2015-20 PF | 2021-26 PF | Verdict |
|---|---|---|---|
| Donchian H1 N100 (no filter) | 1.12 | 1.19 | ok, total +55R over 12 years |
| **Donchian H1 N100 + daily 200-SMA trend filter (live)** | **1.76** | **1.24** | **adopted**, total +82R, half the trades |
| Donchian H4 N55 / N100 | 0.93 / 1.15 | 1.23 / 1.43 | weak in-sample |
| Turtle D1 20 / 55-day | 0.85 / 0.97 | 1.21 / 4.40 | too few trades, weak in-sample |
| Keltner H1 breakout | 0.96-1.03 | 1.13-1.17 | weak |
| Connors RSI(2) mean reversion D1/H4 | 0.52-0.80 | 0.68-0.94 | fails on gold |
| Donchian M15 N400 (Jul22-Jun24 / Jul24-Oct26) | 1.18 | 1.45 | promising, only 4 years of data |

Trend filter robustness: PF > 1 in both periods for 15/15 settings (N 70/100/150 x SMA 50-250).
Cost stress (filtered): PF 1.63/1.20 at 2x costs, 1.46/1.17 at 3x.
Live module parity (filtered signals vs backtest, last 8,000 H1 bars): 192/192 identical.

## Round 3: M15 with full history (2015-2026)
After setting MT5 "Max bars in chart" to Unlimited and restarting MT5, M15 history back to 2004 became available.

| 2015-2026, daily 200-SMA filter | 2015-20 PF | 2021-26 PF | Total | Max DD | Trades/yr |
|---|---|---|---|---|---|
| H1 N100 | 1.76 | 1.24 | +82R | 27R | ~32 |
| M15 N400 | 1.51 | 1.25 | +132R | 39.5R | ~62 |

- M15 with filter: PF > 1 in both periods for N 200-800.
- Cost stress, M15: 2x costs PF 1.28 / 1.19, 3x costs PF 1.02 / 1.08 (about breakeven). H1 holds at 3x (1.46 / 1.17).
- H1 and M15 are highly correlated (monthly R correlation 0.76, 52% of M15 trades overlap an H1 trade).
  So only ONE runs at a time; the user picks it in Risk Settings (`donchian_timeframe`).
- Live parity, M15 module vs backtest (last 6,000 bars): 47/47 signals identical.

## Round 4: channel length x daily-SMA grid (Nov 2026, H1 2016-2026)
90 combinations: channel 30-200 hours x daily SMA 30-250 days; stop 3 ATR, trail 4 ATR; costs as above.
Inputs: `xau_H1_full.csv` / `xau_D1_full.csv` (same export as round 2).

- **Not a lucky spike:** 98% of the 90 cells have PF > 1 in both 2016-20 and 2021-26. Median PF 1.34, median max drawdown 33R.
  The plateau is flat: neighbouring cells look alike, the best-looking cells are not isolated peaks.
- **Weak corners** (refused when saving settings): SMA 30 for every channel (PF 1.07-1.32, 0.93-1.18 at 3x costs);
  SMA 50/60 with channel under 85h (PF at 3x costs 0.99-1.09); channel 30h; channel under 70h with SMA over 120.
- **Walk-forward (pick the best combo on past years, trade the next year, 2019-2026):** out-of-sample total
  66.5R (by PF) / 53.4R (by MAR) vs 67.4R for fixed 100h/200d and 67.6R for the *median* combo.
  => Optimising the two numbers adds no out-of-sample value. Choose from the plateau, do not chase the best cell.
- **Is the entry timing real?** Random entries with the same count, same daily-trend filter and same stops give
  PF median 1.10-1.13 (95th percentile 1.22-1.26); the actual strategy (PF 1.38-1.50) beat all 150 random runs for
  each of four tested combos. Part of the profit comes from the trend filter + trailing exit, the breakout adds the rest.
- **Look-ahead test:** 80 (bar, params) cases recomputed from past data only: 0 differences.
- Bootstrap (resampling trades) 5th-percentile PF is above 1.0 for 72 of 90 cells (PF at 3x costs above 1.0 for 84 of 90); for 100h/200d it is 1.17, 40h/100d 1.13.
- Not tested: M15 (history limited by MT5 max bars), other symbols, other stop/trail multiples together with these grids.

## Round 5 (Nov 2026): other strategies and the merged portfolio
Data: `xau_H1.csv`, `xau_H4.csv` (Jan 2016 -> Nov 2026) and `xau_M15.csv` (Aug 2022 -> Nov 2026, MT5 100,000-bar limit),
exported with yearly `copy_rates_range` calls. Same engine and costs as before. Scripts: `london_bt.py`, `supertrend_bt.py`,
`sweep_bt.py`, `pullback_bt.py`, `merge_bt.py` (results in `results_*.csv`).

| Strategy (all variants tested) | Verdict |
|---|---|
| London breakout H1/H4/M15 (72 variants) | Loses: best PF 1.05 (one H4 variant, +28R in 10 years), H1/M15 never profitable |
| Liquidity sweep H1/H4/M15 (72 variants) | Loses without a trend filter (PF 0.72-0.90); filtered versions have 6-70 trades/yr and an unstable result |
| Supertrend + EMA H4 (ST 10/2, EMA200, stop 3 ATR, exit on flip) | PF 1.39, +55R, max DD 9R, holds at 3x costs (PF 1.30). H1 PF 1.01-1.20, M15 mixed |
| Trend pullback to EMA20 in a daily trend (H4, stop 2 ATR, trail 3 ATR) | PF 1.45, +63R, DD 13R, 3x costs PF 1.34. H1 PF 1.19 (3x costs 1.04), M15 PF 1.28 but DD 59R |

Merged (`merge_bt.py`, 1R = one trade's stop distance, equal weight):

| 2016-2026 | Trades/yr | PF | Total | Max DD |
|---|---|---|---|---|
| Donchian H1 (the previous live strategy) | 35 | 1.37 | +67R | 27R |
| + Supertrend H4 | 70 | 1.38 | +122R | 33R |
| **+ Pullback H4 (live: PORTFOLIO_TREND)** | **96** | **1.40** | **+185R** | **41R** |
| + Pullback H1 | 161 | 1.31 | +254R | 75R |

Pullback H1 and M15 add return but raise drawdown disproportionately (monthly R correlation with Donchian H1 is 0.74), so
they are not in the live portfolio. Up to 3 trades can be open at once.

Caveats: 2021-22 lost in every H1/H4 strategy; most of the profit comes from 2019-20 and 2024-25. Results after 2023 are
inflated by the gold rally - expect a lower PF going forward. Maximum drawdown is measured on closed-trade order, not tick
equity.

Live code: `app/services/portfolio_trend.py`. Parity check (live signal functions vs the backtest signals on the same bars,
last 3,000 H4 candles): Supertrend 0 mismatches / 68 signals, Pullback 0 mismatches / 195 signals.
