"""Tests for the Donchian Trend strategy module and the consecutive-loss breaker fix."""
from datetime import datetime, timedelta

from app.domain.models import Candle, TickerData, TradeSide
from app.services import donchian_trend as dt


def _candles(closes, spread=1.0):
    t0 = datetime(2026, 1, 1)
    return [Candle(timestamp=t0 + timedelta(hours=i), open=c, high=c + spread, low=c - spread,
                   close=c, volume=100, timeframe="H1", is_closed=True) for i, c in enumerate(closes)]


def _ticker(bid):
    kw = dict(symbol="XAUUSD", bid=bid, ask=bid + 0.3, spread_pips=3.0, timestamp=datetime.now())
    for k, f in TickerData.model_fields.items():
        if f.is_required() and k not in kw:
            kw[k] = 0.0
    return TickerData(**{k: v for k, v in kw.items() if k in TickerData.model_fields})


FLAT = [2000.0] * 120


def test_buy_on_fresh_breakout():
    s, _ = dt.breakout_signal(_candles(FLAT + [2010.0]))
    assert s == 1


def test_sell_on_fresh_breakdown():
    s, _ = dt.breakout_signal(_candles(FLAT + [1990.0]))
    assert s == -1


def test_no_repeat_signal_on_second_bar_beyond_channel():
    s, _ = dt.breakout_signal(_candles(FLAT + [2010.0, 2015.0]))
    assert s == 0


def test_no_signal_inside_channel():
    s, _ = dt.breakout_signal(_candles(FLAT + [2000.5]))
    assert s == 0


def test_forming_candle_is_ignored():
    c = _candles(FLAT + [2010.0])
    c[-1].is_closed = False  # breakout only on an unclosed bar -> no trade yet
    rep = dt.evaluate("XAUUSD", c, _ticker(2010.0))
    assert rep.decision == TradeSide.HOLD


def test_report_stop_is_3_atr_and_rr_passes():
    c = _candles(FLAT + [2010.0])
    rep = dt.evaluate("XAUUSD", c, _ticker(2010.0), daily=_daily(UP_DAYS))
    assert rep.decision == TradeSide.BUY
    atr = dt.atr_series(c)[-1]
    assert abs((rep.suggested_entry - rep.suggested_sl) - 3 * atr) < 0.02
    assert rep.risk_reward_ratio >= 2.0
    assert rep.setup_name == dt.SETUP_NAME


def test_spread_block():
    rep = dt.evaluate("XAUUSD", _candles(FLAT + [2010.0]), _ticker(2010.0), max_spread_price=0.1, daily=_daily(UP_DAYS))
    assert rep.decision == TradeSide.HOLD
    assert "SPREAD_BLOCKED" in rep.rejection_reasons


def test_trailing_only_moves_in_favour():
    c = _candles(FLAT + [2010.0, 2030.0])
    atr = dt.atr_series(c)[-1]
    new = dt.trailing_stop(c, True, current_sl=1990.0)
    assert new is not None and abs(new - (2030.0 - 4 * atr)) < 1e-9
    assert dt.trailing_stop(c, True, current_sl=new + 1) is None
    short = dt.trailing_stop(c, False, current_sl=2100.0)
    assert short is not None and short < 2100.0


def test_consecutive_loss_zero_means_disabled_and_breaker_expires(monkeypatch):
    from tests.test_entry_guards import _evaluate, _report, _has
    from app.services.trade_learner import trade_learner

    def losses(hours_ago):
        ts = (datetime.now() - timedelta(hours=hours_ago)).isoformat()
        monkeypatch.setattr(trade_learner, "get_recent_closed_trades",
                            lambda limit=10, since_timestamp=None: [{"pnl_usd": -5.0, "close_timestamp": ts}] * 5)

    losses(1)
    assert not _has(_evaluate(_report(), max_consec=0), "consecutive losses"), "0 must disable the breaker"
    assert _has(_evaluate(_report(), max_consec=3), "consecutive losses")
    losses(30)
    assert not _has(_evaluate(_report(), max_consec=3), "consecutive losses"), "breaker must release after 24h"


# ── Daily trend filter ──────────────────────────────────────────────────────
def _daily(closes):
    t0 = datetime(2025, 1, 1)
    return [Candle(timestamp=t0 + timedelta(days=i), open=c, high=c + 1, low=c - 1,
                   close=c, volume=100, timeframe="D1", is_closed=True) for i, c in enumerate(closes)]


UP_DAYS = [1000.0 + i for i in range(210)]      # last close above 200-day avg
DOWN_DAYS = [2000.0 - i for i in range(210)]    # last close below 200-day avg


def test_daily_trend_direction():
    assert dt.daily_trend(_daily(UP_DAYS))[0] == 1
    assert dt.daily_trend(_daily(DOWN_DAYS))[0] == -1
    assert dt.daily_trend(_daily(UP_DAYS[:50]))[0] == 0  # not enough history


def test_buy_allowed_in_uptrend_blocked_in_downtrend():
    c = _candles(FLAT + [2010.0])
    assert dt.evaluate("XAUUSD", c, _ticker(2010.0), daily=_daily(UP_DAYS)).decision == TradeSide.BUY
    rep = dt.evaluate("XAUUSD", c, _ticker(2010.0), daily=_daily(DOWN_DAYS))
    assert rep.decision == TradeSide.HOLD and "TREND_FILTER" in rep.rejection_reasons


def test_sell_allowed_in_downtrend_blocked_in_uptrend():
    c = _candles(FLAT + [1990.0])
    assert dt.evaluate("XAUUSD", c, _ticker(1990.0), daily=_daily(DOWN_DAYS)).decision == TradeSide.SELL
    assert dt.evaluate("XAUUSD", c, _ticker(1990.0), daily=_daily(UP_DAYS)).decision == TradeSide.HOLD


def test_no_trade_when_daily_trend_unknown():
    c = _candles(FLAT + [2010.0])
    assert dt.evaluate("XAUUSD", c, _ticker(2010.0), daily=None).decision == TradeSide.HOLD


# ── Timeframe selection (H1 / M15, one at a time) ───────────────────────────
def test_variant_selection_and_fallback():
    assert dt.variant("M15") == ("M15", dt.VARIANTS["M15"])
    assert dt.variant("h1")[0] == "H1"
    assert dt.variant("M5")[0] == "H1"  # unsupported -> safe default


def test_m15_uses_400_bar_channel():
    flat = [2000.0] * 420
    c = _candles(flat + [2010.0])
    rep = dt.evaluate("XAUUSD", c, _ticker(2010.0), daily=_daily(UP_DAYS), tf="M15")
    assert rep.decision == TradeSide.BUY and rep.timeframe == "M15" and "M15 400-bar" in rep.setup_name
    # only 150 candles: not enough for the M15 channel, so no trade
    rep2 = dt.evaluate("XAUUSD", _candles([2000.0] * 150 + [2010.0]), _ticker(2010.0), daily=_daily(UP_DAYS), tf="M15")
    assert rep2.decision == TradeSide.HOLD
