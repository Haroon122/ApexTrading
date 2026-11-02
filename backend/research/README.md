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
