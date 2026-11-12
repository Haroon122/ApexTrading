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


# ── User-adjustable channel / trend average + save-time validation ───────────
from app.domain.models import RiskSettings


def test_defaults_are_safe_and_unchanged():
    rs = RiskSettings()
    assert (rs.donchian_channel_hours, rs.donchian_trend_sma) == (100, 200)
    assert dt.validate_params(100, 200, "H1") is None
    assert dt.effective_params(rs, "H1") == (100, 200)
    assert dt.effective_params(rs, "M15") == (400, 200)   # hours -> 4x candles on M15


def test_backtested_good_values_are_allowed():
    for ch, sma in [(40, 100), (70, 100), (100, 100), (150, 100), (100, 50), (85, 60), (200, 250), (70, 200), (55, 80)]:
        assert dt.validate_params(ch, sma, "H1") is None, (ch, sma)


def test_too_short_channel_or_average_is_refused():
    assert "too short" in dt.validate_params(20, 100, "H1")
    assert "too short" in dt.validate_params(30, 100, "H1")
    assert "too short" in dt.validate_params(100, 30, "H1")
    assert dt.validate_params(40, 100, "M15") is not None        # M15 floor is 50h


def test_untested_long_values_are_refused():
    assert dt.validate_params(500, 100, "H1") is not None
    assert dt.validate_params(100, 400, "H1") is not None


def test_short_channel_with_fast_average_is_refused():
    assert "fast 50-day" in dt.validate_params(70, 50, "H1")
    assert "fast 60-day" in dt.validate_params(55, 60, "H1")
    assert dt.validate_params(85, 60, "H1") is None


def test_short_channel_with_slow_average_is_refused():
    msg = dt.validate_params(40, 200, "H1")
    assert msg and "short breakout lookback" in msg
    assert dt.validate_params(40, 100, "H1") is None


def test_engine_ignores_invalid_settings_and_uses_defaults():
    rs = RiskSettings(donchian_channel_hours=5, donchian_trend_sma=5)   # e.g. hand-edited json
    assert dt.effective_params(rs, "H1") == (100, 200)


def test_custom_channel_changes_the_breakout_window():
    # 60 flat candles then a spike: a 40h channel sees the breakout, the default 100h can't (not enough data)
    closes = [2000.0] * 60 + [2010.0]
    c = _candles(closes)
    short = RiskSettings(donchian_channel_hours=40, donchian_trend_sma=100)
    up = _daily(UP_DAYS)
    assert dt.evaluate("XAUUSD", c, _ticker(2010.0), daily=up, rs=short).decision == TradeSide.BUY
    assert dt.evaluate("XAUUSD", c, _ticker(2010.0), daily=up).decision == TradeSide.HOLD
    assert dt.evaluate("XAUUSD", c, _ticker(2010.0), daily=up, rs=short).setup_name == "DONCHIAN TREND: H1 40-bar Breakout"


def test_custom_sma_changes_trend_filter():
    # 150 days at 2000, 60 days at 1000, then 10 days at 1500:
    # above its 50-day average (~1100) but below its 200-day average (~1675)
    d = _daily([2000.0] * 150 + [1000.0] * 60 + [1500.0] * 10)
    assert dt.daily_trend(d, 50)[0] == 1
    assert dt.daily_trend(d, 200)[0] == -1
    c = _candles([2000.0] * 60 + [2010.0])
    rs = RiskSettings(donchian_channel_hours=40, donchian_trend_sma=100)
    assert dt.evaluate("XAUUSD", c, _ticker(2010.0), daily=d, rs=rs).decision == TradeSide.BUY      # 100d avg ~1425 < 1500


def test_fetch_sizes_follow_settings():
    class A:
        def __init__(self): self.calls = []
        def fetch_candles(self, sym, tf, n): self.calls.append((tf, n)); return []
    a = A(); rs = RiskSettings(donchian_channel_hours=70, donchian_trend_sma=100)
    dt.fetch_bars(a, "XAUUSD", "H1", rs=rs); dt.fetch_daily(a, "XAUUSD", rs=rs)
    dt.fetch_bars(a, "XAUUSD", "M15", rs=rs); dt.fetch_bars(a, "XAUUSD", "H1")
    assert a.calls == [("H1", 270), ("D1", 110), ("M15", 480), ("H1", 300)]


def test_save_endpoint_refuses_bad_values_without_touching_live_settings():
    from app.api.v1.endpoints import update_risk_settings
    from app.services.trade_manager import trade_manager
    before = trade_manager.risk_settings.model_dump()
    res = update_risk_settings(RiskSettings(strategy_profile="DONCHIAN_TREND", donchian_channel_hours=20, donchian_trend_sma=100))
    assert res["success"] is False and res["message"].startswith("Not saved:")
    res = update_risk_settings(RiskSettings(strategy_profile="DONCHIAN_TREND", donchian_channel_hours=100, donchian_trend_sma=30))
    assert res["success"] is False and "too short" in res["message"]
    assert trade_manager.risk_settings.model_dump() == before


def test_out_of_range_values_do_not_break_settings_loading():
    # A hand-edited file must still load (otherwise every other setting would reset to defaults)
    rs = RiskSettings(donchian_channel_hours=5000, donchian_trend_sma=1, max_open_positions=2)
    assert rs.max_open_positions == 2
    assert dt.effective_params(rs, "H1") == (100, 200)


def test_daily_fetch_uses_same_fallback_as_evaluate_on_m15():
    class A:
        def fetch_candles(self, sym, tf, n): self.n = n; return []
    a = A(); rs = RiskSettings(donchian_channel_hours=45, donchian_trend_sma=100)   # valid on H1, invalid on M15
    dt.fetch_daily(a, "XAUUSD", rs=rs, tf="M15")
    assert a.n == 210          # fell back to the 200-day default, same as evaluate()
    dt.fetch_daily(a, "XAUUSD", rs=rs, tf="H1")
    assert a.n == 110
