"""Tests for PORTFOLIO_TREND: Supertrend H4, Pullback H4 and the multi-strategy pipeline."""
from datetime import datetime, timedelta

import pytest

from app.adapters.mt5_adapter import mt5_adapter
from app.domain.models import (
    AccountInfo, BotStatus, Candle, OrderType, RiskSettings, TickerData, TradeRecord, TradeSide, TradeState,
)
from app.services import donchian_trend as dt
from app.services import portfolio_trend as pt
from app.services.trade_manager import trade_manager

T0 = datetime(2026, 1, 5)


def _bars(rows, hours=4, t0=T0, tf="H4"):
    """rows: (open, high, low, close)"""
    return [Candle(timestamp=t0 + timedelta(hours=hours * i), open=o, high=h, low=l, close=c, volume=1,
                   timeframe=tf, is_closed=True) for i, (o, h, l, c) in enumerate(rows)]


def _from_closes(closes, wick=0.5, hours=4, tf="H4"):
    rows, prev = [], closes[0]
    for c in closes:
        rows.append((prev, max(prev, c) + wick, min(prev, c) - wick, c))
        prev = c
    return _bars(rows, hours=hours, tf=tf)


def _days(closes):
    return [Candle(timestamp=datetime(2025, 1, 1) + timedelta(days=i), open=c, high=c + 1, low=c - 1, close=c,
                   volume=1, timeframe="D1", is_closed=True) for i, c in enumerate(closes)]


UP_DAYS = _days([1000.0 + i for i in range(230)])          # last close far above its 200-day average
DOWN_DAYS = _days([2000.0 - i for i in range(230)])


def _ticker(bid, ts=None):
    kw = dict(symbol="XAUUSD", bid=bid, ask=bid + 0.3, spread_pips=3.0, timestamp=ts or datetime.now())
    for k, f in TickerData.model_fields.items():
        if f.is_required() and k not in kw:
            kw[k] = 0.0
    return TickerData(**{k: v for k, v in kw.items() if k in TickerData.model_fields})


def _pullback_bars(side=1, n=200, hours=4, tf="H4"):
    """Steady trend, then a last candle that dips to EMA20 and closes back on the trend side."""
    base = [2000.0 + side * 2.0 * i for i in range(n)]
    bars = _from_closes(base, wick=0.3, hours=hours, tf=tf)
    prev = base[-1]
    close = prev + side * 1.0
    ema = pt.ema_series(base + [close], pt.PB_EMA_FAST)[-1]
    ts = bars[-1].timestamp + timedelta(hours=hours)
    if side == 1:
        last = Candle(timestamp=ts, open=close - 0.5, high=close + 0.3, low=ema - 0.5, close=close, volume=1,
                      timeframe=tf, is_closed=True)
    else:
        last = Candle(timestamp=ts, open=close + 0.5, high=ema + 0.5, low=close - 0.3, close=close, volume=1,
                      timeframe=tf, is_closed=True)
    return bars + [last]


# ── helpers / identification ─────────────────────────────────────────────

def test_strategy_identification_and_order_tags_roundtrip():
    assert pt.strategy_of(dt.setup_name("H1", 100)) == "DONCHIAN"
    assert pt.strategy_of(pt.setup_st("H4")) == "SUPERTREND"
    assert pt.strategy_of(pt.setup_pb("H4")) == "PULLBACK"
    assert pt.strategy_of("Manual Execution") is None and pt.strategy_of("Unknown") is None and pt.strategy_of(None) is None
    for key in pt.KEYS:
        setup = {"DONCHIAN": dt.setup_name("H1"), "SUPERTREND": pt.setup_st("H4"), "PULLBACK": pt.setup_pb("H4")}[key]
        tag = pt.tag_for_setup(setup)
        assert tag and len(tag) <= 31
        assert pt.strategy_of(pt.setup_for_tag(tag)) == key
    assert pt.tag_for_setup("SETUP D: Bullish BOS") is None and pt.setup_for_tag("ApexTrader AI Order") is None


def test_enabled_keys_follow_settings():
    rs = RiskSettings(portfolio_supertrend_enabled=False)
    assert pt.enabled_keys(rs) == ["DONCHIAN", "PULLBACK"]
    assert not pt.is_active(rs) and pt.is_active(RiskSettings(strategy_profile="PORTFOLIO_TREND"))


def test_ema_matches_pandas_ewm():
    pd = pytest.importorskip("pandas")
    vals = [3.0, 5.0, 4.0, 8.0, 7.5, 9.0, 12.0, 11.0]
    expected = pd.Series(vals).ewm(span=5, adjust=False).mean().tolist()
    assert pt.ema_series(vals, 5) == pytest.approx(expected)


# ── Supertrend ──────────────────────────────────────────────────────────

def test_supertrend_follows_trend_direction():
    up = _from_closes([2000.0 + i for i in range(60)])
    dirs, line = pt.supertrend(up)
    assert dirs[-1] == 1 and line[-1] < up[-1].close
    down = _from_closes([2000.0 - i for i in range(60)])
    dirs, line = pt.supertrend(down)
    assert dirs[-1] == -1 and line[-1] > down[-1].close


def _zigzag(n_cycles=14, drift=1, with_leg=40, against_leg=12):
    """Trend with pullbacks: `with_leg` bars with the drift, then `against_leg` bars against it."""
    closes, p = [2000.0], 2000.0
    for _ in range(n_cycles):
        for _ in range(with_leg):
            p += 3.0 * drift
            closes.append(p)
        for _ in range(against_leg):
            p -= 3.0 * drift
            closes.append(p)
    return _from_closes(closes, wick=1.0)


def test_supertrend_signals_only_on_flips_and_respect_ema():
    bars = _zigzag(n_cycles=14)
    assert len(bars) >= pt.ST_MIN_BARS
    dirs, _ = pt.supertrend(bars)
    seen = {1: 0, -1: 0}
    skipped = 0
    for i in range(pt.ST_MIN_BARS, len(bars)):
        sig, _, info = pt.supertrend_signal(bars[:i + 1])
        flip = dirs[i] != dirs[i - 1]
        if sig != 0:
            assert flip and sig == dirs[i]
            assert (sig == 1) == (info["close"] > info["ema"])
            seen[sig] += 1
        elif dirs[i] != dirs[i - 1]:
            skipped += 1                       # flipped against EMA200: must NOT trade
    assert seen[1] >= 1 and seen[-1] == 0      # an up-trending series only produces BUY flips
    assert skipped >= 1


def test_supertrend_needs_enough_history():
    sig, why, info = pt.supertrend_signal(_zigzag(2))
    assert sig == 0 and "Need" in why and info == {}


def _find_signal_window(want):
    bars = _zigzag(n_cycles=16, drift=want)
    for i in range(pt.ST_MIN_BARS, len(bars)):
        sig, _, _ = pt.supertrend_signal(bars[:i + 1])
        if sig == want:
            return bars[:i + 1]
    return None


@pytest.mark.parametrize("want", [1, -1])
def test_supertrend_report_has_3atr_stop_and_far_tp(want):
    window = _find_signal_window(want)
    assert window is not None, "synthetic trend produced no EMA-aligned flip"
    rep = pt.evaluate("SUPERTREND", "XAUUSD", window, None, _ticker(window[-1].close))
    assert rep.decision == (TradeSide.BUY if want == 1 else TradeSide.SELL)
    assert rep.setup_name == pt.setup_st("H4") and rep.timeframe == "H4"
    atr = dt.atr_series(window)[-1]
    assert abs(abs(rep.suggested_entry - rep.suggested_sl) - 3.0 * atr) < 0.02
    assert rep.risk_reward_ratio >= 2.0 and rep.signal_candle_ts == window[-1].timestamp


def test_supertrend_exit_when_flipped_against_open_trade():
    up = _from_closes([2000.0 + i for i in range(450)])
    assert pt.manage_action("SUPERTREND", up, True, 1900.0) is None            # trend still up -> keep
    down = up + _from_closes([2449.0 - 4 * i for i in range(1, 30)])
    for k in range(len(up), len(down)):
        act = pt.manage_action("SUPERTREND", down[:k + 1], True, 1900.0)
        if act:
            assert act[0] == "CLOSE" and "SUPERTREND_FLIP" in act[1]
            break
    else:
        pytest.fail("Supertrend never closed a long after a hard reversal")
    # a SELL trade is not closed by the same bearish trend
    assert pt.manage_action("SUPERTREND", down, False, 3000.0) is None


# ── Pullback ────────────────────────────────────────────────────────────

def test_pullback_buy_in_uptrend():
    bars = _pullback_bars(+1)
    sig, why, info = pt.pullback_signal(bars, UP_DAYS)
    assert sig == 1, why
    assert info["ema_fast"] > info["ema_slow"]


def test_pullback_sell_in_downtrend():
    bars = _pullback_bars(-1)
    sig, why, _ = pt.pullback_signal(bars, DOWN_DAYS)
    assert sig == -1, why


def test_pullback_blocked_against_daily_trend_or_unknown_trend():
    assert pt.pullback_signal(_pullback_bars(+1), DOWN_DAYS)[0] == 0
    assert pt.pullback_signal(_pullback_bars(-1), UP_DAYS)[0] == 0
    assert pt.pullback_signal(_pullback_bars(+1), UP_DAYS[:50])[0] == 0        # < 200 daily candles
    assert pt.pullback_signal(_pullback_bars(+1), None)[0] == 0


def test_pullback_needs_touch_and_rejection_candle():
    bars = _pullback_bars(+1)
    far = bars[:-1] + [bars[-1].model_copy(update={"low": bars[-1].close - 0.2})]            # never touched EMA20
    assert pt.pullback_signal(far, UP_DAYS)[0] == 0
    bearish = bars[:-1] + [bars[-1].model_copy(update={"open": bars[-1].close + 1.0})]       # closed down on the bar
    assert pt.pullback_signal(bearish, UP_DAYS)[0] == 0


def test_pullback_report_stop_is_2atr():
    bars = _pullback_bars(+1)
    rep = pt.evaluate("PULLBACK", "XAUUSD", bars, UP_DAYS, _ticker(bars[-1].close))
    assert rep.decision == TradeSide.BUY and rep.setup_name == pt.setup_pb("H4")
    atr = dt.atr_series(bars)[-1]
    assert abs((rep.suggested_entry - rep.suggested_sl) - 2.0 * atr) < 0.02


def test_forming_candle_never_triggers_a_portfolio_signal():
    bars = _pullback_bars(+1)
    bars[-1].is_closed = False
    rep = pt.evaluate("PULLBACK", "XAUUSD", bars, UP_DAYS, _ticker(bars[-1].close))
    assert rep.decision == TradeSide.HOLD


def test_spread_block_applies_to_new_strategies():
    bars = _pullback_bars(+1)
    rep = pt.evaluate("PULLBACK", "XAUUSD", bars, UP_DAYS, _ticker(bars[-1].close), max_spread_price=0.1)
    assert rep.decision == TradeSide.HOLD and "SPREAD_BLOCKED" in rep.rejection_reasons


def test_pullback_trail_is_3atr_and_only_moves_in_favour():
    bars = _from_closes([2000.0 + i for i in range(80)])
    atr = dt.atr_series(bars)[-1]
    act = pt.manage_action("PULLBACK", bars, True, 1500.0)
    assert act[0] == "TRAIL" and act[1] == pytest.approx(bars[-1].close - 3.0 * atr)
    assert pt.manage_action("PULLBACK", bars, True, bars[-1].close) is None              # stop already tighter
    sell = pt.manage_action("PULLBACK", bars, False, 9999.0)
    assert sell[1] == pytest.approx(bars[-1].close + 3.0 * atr)


def test_donchian_trail_uses_4atr_in_portfolio():
    bars = _from_closes([2000.0 + i for i in range(80)], tf="H1", hours=1)
    atr = dt.atr_series(bars)[-1]
    act = pt.manage_action("DONCHIAN", bars, True, 1500.0)
    assert act[1] == pytest.approx(bars[-1].close - 4.0 * atr)


# ── staleness ───────────────────────────────────────────────────────────

def test_signal_age_and_limits():
    bars = _pullback_bars(+1)
    close_time = bars[-1].timestamp + timedelta(hours=4)
    assert pt.signal_age_minutes(bars, "H4", _ticker(1.0, close_time + timedelta(minutes=2))) == pytest.approx(2.0)
    assert pt.signal_age_minutes(bars, "H4", None) is None
    assert pt.max_signal_age_minutes("H1") == 10.0 and pt.max_signal_age_minutes("H4") == pytest.approx(36.0)


# ── end-to-end pipeline (paper mode, fake market data) ─────────────────────

class _Market:
    def __init__(self, h4, daily, h1=None, ticker_ts=None):
        self.h4, self.daily = h4, daily
        self.h1 = h1 if h1 is not None else _from_closes([2000.0] * 400, tf="H1", hours=1)
        self.ticker_ts = ticker_ts

    def fetch_candles(self, symbol, tf, count=200, *a, **k):
        data = {"H4": self.h4, "H1": self.h1, "D1": self.daily}.get(tf, [])
        return data[-count:]

    def ticker(self):
        last = self.h4[-1]
        ts = self.ticker_ts or (last.timestamp + timedelta(hours=4, minutes=1))
        return _ticker(last.close, ts)


@pytest.fixture
def market(monkeypatch):
    rs = trade_manager.risk_settings
    rs.strategy_profile = "PORTFOLIO_TREND"
    rs.news_filter_enabled = False
    rs.portfolio_donchian_enabled = False       # only H4 strategies in these scenarios
    rs.portfolio_supertrend_enabled = False
    rs.portfolio_pullback_enabled = True
    rs.portfolio_donchian_tf, rs.portfolio_supertrend_tf, rs.portfolio_pullback_tf = "H1", "H4", "H4"
    rs.max_open_positions = 3
    rs.lot_sizing_mode, rs.max_allowed_lot = "DYNAMIC_RISK", 5.0
    rs.max_spread_pips = 50.0
    rs.max_trades_per_day = 99
    rs.max_consecutive_losses = 0
    rs.auto_trade_enabled = True
    monkeypatch.setattr(trade_manager, "bot_status", BotStatus.RUNNING)
    monkeypatch.setattr(trade_manager, "active_trades", {})
    monkeypatch.setattr(trade_manager, "portfolio_signals", {})
    monkeypatch.setattr(trade_manager, "_check_execution_safety", lambda symbol: (True, "PAPER_MODE"))
    acct = AccountInfo(account_number=1, balance=10000.0, equity=10000.0, margin=0.0, free_margin=10000.0,
                       margin_level=0.0, daily_pnl_usd=0.0, weekly_pnl_usd=0.0, drawdown_pct=0.0, open_positions_count=0)
    state = {"m": None}
    monkeypatch.setattr(mt5_adapter, "get_account_info", lambda: acct)
    monkeypatch.setattr(mt5_adapter, "get_ticker", lambda symbol: state["m"].ticker())
    monkeypatch.setattr(mt5_adapter, "fetch_candles", lambda *a, **k: state["m"].fetch_candles(*a, **k))
    monkeypatch.setattr(mt5_adapter, "get_positions", lambda: list(trade_manager.active_trades.values()))
    monkeypatch.setattr(type(mt5_adapter), "is_connected", False, raising=False)

    def use(m):
        state["m"] = m
        return m
    return use


def test_pipeline_opens_pullback_trade_with_half_percent_risk(market):
    m = market(_Market(_pullback_bars(+1), UP_DAYS))
    res = trade_manager._portfolio_pipeline("XAUUSD")
    assert res["success"], res["reason"]
    trade = list(trade_manager.active_trades.values())[0]
    assert trade.setup_name == pt.setup_pb("H4") and trade.side == TradeSide.BUY and trade.timeframe == "H4"
    # 0.5% of 10,000 = $50 risk over a 2xATR stop (gold: $100 per lot per $1 move)
    stop = abs(trade.entry_price - trade.sl_price)
    assert trade.lots == pytest.approx(round(50.0 / (stop * 100.0), 2), abs=0.011)
    assert stop == pytest.approx(2.0 * dt.atr_series(m.h4)[-1], abs=0.02)


def test_same_candle_is_never_traded_twice(market):
    market(_Market(_pullback_bars(+1), UP_DAYS))
    assert trade_manager._portfolio_pipeline("XAUUSD")["success"]
    res = trade_manager._portfolio_pipeline("XAUUSD")                     # still the same signal candle
    assert not res["success"] and len(trade_manager.active_trades) == 1
    trade_manager.active_trades.clear()                                    # even after the trade is gone...
    res = trade_manager._portfolio_pipeline("XAUUSD")
    assert not res["success"] and "already traded" in res["reason"]
    assert not trade_manager.active_trades


def test_stale_signal_is_skipped(market):
    bars = _pullback_bars(+1)
    late = bars[-1].timestamp + timedelta(hours=4, minutes=90)             # 90 minutes after the candle closed
    market(_Market(bars, UP_DAYS, ticker_ts=late))
    res = trade_manager._portfolio_pipeline("XAUUSD")
    assert not res["success"] and "expired" in res["reason"] and not trade_manager.active_trades


def test_hedge_guard_blocks_opposite_direction(market):
    market(_Market(_pullback_bars(+1), UP_DAYS))
    trade_manager.active_trades["999"] = TradeRecord(
        trade_id="999", symbol="XAUUSD", side=TradeSide.SELL, order_type=OrderType.MARKET, entry_price=2500.0,
        sl_price=2510.0, tp_price=2400.0, lots=0.01, state=TradeState.OPEN, open_time=datetime.now(),
        setup_name=pt.setup_st("H4"), timeframe="H4")
    res = trade_manager._portfolio_pipeline("XAUUSD")
    assert not res["success"] and "HEDGE GUARD" in " ".join(res.get("risk_reasons", []))
    assert len(trade_manager.active_trades) == 1


def test_strategy_with_open_trade_does_not_open_another(market):
    market(_Market(_pullback_bars(+1), UP_DAYS))
    trade_manager.active_trades["1000"] = TradeRecord(
        trade_id="1000", symbol="XAUUSD", side=TradeSide.BUY, order_type=OrderType.MARKET, entry_price=2300.0,
        sl_price=2290.0, tp_price=2500.0, lots=0.01, state=TradeState.OPEN, open_time=datetime.now(),
        setup_name=pt.setup_pb("H4"), timeframe="H4")
    res = trade_manager._portfolio_pipeline("XAUUSD")
    assert not res["success"] and "already has an open trade" in res["reason"]


def test_disabled_strategy_is_not_traded(market):
    trade_manager.risk_settings.portfolio_pullback_enabled = False
    market(_Market(_pullback_bars(+1), UP_DAYS))
    res = trade_manager._portfolio_pipeline("XAUUSD")
    assert not res["success"] and "no portfolio strategy is enabled" in res["reason"]


def test_two_strategies_can_trade_the_same_candle_without_cooldown_or_proximity_blocks(market):
    # Pullback H4 BUY and a Donchian H1 BUY breakout on the same close, at nearly the same price.
    h1 = _from_closes([2000.0] * 300 + [2030.0], tf="H1", hours=1)
    h4 = _pullback_bars(+1)
    h1_ts = h4[-1].timestamp + timedelta(hours=4) - timedelta(hours=1)
    h1 = [c.model_copy(update={"timestamp": h1_ts - timedelta(hours=len(h1) - 1 - i)}) for i, c in enumerate(h1)]
    trade_manager.risk_settings.portfolio_donchian_enabled = True
    market(_Market(h4, UP_DAYS, h1=h1))
    res = trade_manager._portfolio_pipeline("XAUUSD")
    assert res["success"], res["reason"]
    names = sorted(t.setup_name for t in trade_manager.active_trades.values())
    assert len(names) == 2 and names[0].startswith("DONCHIAN") and names[1].startswith("PULLBACK")


def test_trailing_and_supertrend_exit_are_applied_per_strategy(market, monkeypatch):
    bars = _from_closes([2000.0 + i for i in range(450)])
    market(_Market(bars, UP_DAYS))
    pb = TradeRecord(trade_id="1", symbol="XAUUSD", side=TradeSide.BUY, order_type=OrderType.MARKET,
                     entry_price=2300.0, sl_price=2290.0, tp_price=2800.0, lots=0.01, state=TradeState.OPEN,
                     open_time=datetime.now(), setup_name=pt.setup_pb("H4"), timeframe="H4")
    calls = []
    monkeypatch.setattr(mt5_adapter, "update_sl_tp", lambda tid, new_sl=None, new_tp=None: calls.append((tid, new_sl)) or True)
    trade_manager._manage_portfolio_position("1", pb, True, 2460.0, 0.1, 2, {})
    atr = dt.atr_series(bars[-100:])[-1]
    assert calls and calls[0][1] == pytest.approx(round(bars[-1].close - 3.0 * atr, 2), abs=0.05)
    assert pb.sl_price == calls[0][1]
    # legacy / manual trades are left alone
    manual = pb.model_copy(update={"trade_id": "2", "setup_name": "Manual Execution", "sl_price": 1.0})
    calls.clear()
    trade_manager._manage_portfolio_position("2", manual, True, 2460.0, 0.1, 2, {})
    assert not calls and manual.sl_price == 1.0


# ── settings endpoint ────────────────────────────────────────────────────

def test_save_endpoint_validates_portfolio_settings():
    from app.api.v1.endpoints import update_risk_settings
    before = trade_manager.risk_settings.model_dump()
    res = update_risk_settings(RiskSettings(strategy_profile="PORTFOLIO_TREND", portfolio_donchian_enabled=False,
                                            portfolio_supertrend_enabled=False, portfolio_pullback_enabled=False))
    assert not res["success"] and "at least one" in res["message"]
    res = update_risk_settings(RiskSettings(strategy_profile="PORTFOLIO_TREND", max_open_positions=2))
    assert not res["success"] and "Max Open Positions" in res["message"]
    res = update_risk_settings(RiskSettings(strategy_profile="PORTFOLIO_TREND", max_open_positions=3,
                                            portfolio_risk_per_strategy_pct=5.0))
    assert not res["success"] and "between 0.1% and 2.0%" in res["message"]
    assert trade_manager.risk_settings.model_dump() == before
    res = update_risk_settings(RiskSettings(strategy_profile="PORTFOLIO_TREND", max_open_positions=3))
    assert res["success"] and trade_manager.risk_settings.strategy_profile == "PORTFOLIO_TREND"


# ── user-selectable timeframes ──────────────────────────────────────────

def test_default_and_invalid_timeframes():
    rs = RiskSettings()
    assert [pt.tf_of(k, rs) for k in pt.KEYS] == ["H1", "H4", "H4"]
    rs = RiskSettings(portfolio_donchian_tf="H4", portfolio_supertrend_tf="W1", portfolio_pullback_tf="m15")
    assert pt.tf_of("DONCHIAN", rs) == "H1" and pt.tf_of("SUPERTREND", rs) == "H4" and pt.tf_of("PULLBACK", rs) == "M15"
    assert "cannot run on" in pt.validate_timeframes(rs)
    assert pt.validate_timeframes(RiskSettings(portfolio_donchian_tf="M15", portfolio_supertrend_tf="H1", portfolio_pullback_tf="M15")) is None


def test_every_strategy_timeframe_has_tested_parameters():
    for key, tfs in pt.SUPPORTED_TFS.items():
        for tf in tfs:
            p = pt.params(key, tf)
            assert p["stop_atr"] > 0 and p["exit"]
    assert (pt.ST_PROFILES["H4"]["period"], pt.ST_PROFILES["H4"]["mult"], pt.ST_PROFILES["H4"]["stop_atr"]) == (10, 2.0, 3.0)
    assert (pt.ST_PROFILES["H1"]["period"], pt.ST_PROFILES["H1"]["mult"], pt.ST_PROFILES["H1"]["stop_atr"]) == (14, 3.0, 3.0)
    assert (pt.ST_PROFILES["M15"]["period"], pt.ST_PROFILES["M15"]["mult"], pt.ST_PROFILES["M15"]["stop_atr"]) == (14, 3.0, 2.0)
    assert pt.params("PULLBACK", "H4")["trail_atr"] == 3.0 and pt.params("PULLBACK", "H1")["trail_atr"] == 4.0
    assert pt.params("SUPERTREND", "H1")["trail_atr"] is None


def test_order_tags_carry_the_timeframe():
    for key, tfs in pt.SUPPORTED_TFS.items():
        for tf in tfs:
            setup = pt.setup_for(key, tf)
            tag = pt.tag_for_setup(setup)
            assert tag and len(tag) <= 31
            back = pt.setup_for_tag(tag)
            assert pt.strategy_of(back) == key and pt.timeframe_from_setup(back) == tf
    assert pt.setup_for_tag("APEX:ST-W1") is None and pt.setup_for_tag("APEX:XX-H1") is None


@pytest.mark.parametrize("tf,hours,stop", [("H1", 1, 3.0), ("M15", 0.25, 2.0)])
def test_supertrend_uses_the_profile_of_its_timeframe(tf, hours, stop):
    bars = _zigzag(n_cycles=16, drift=1)
    bars = [c.model_copy(update={"timestamp": T0 + timedelta(hours=hours * i), "timeframe": tf}) for i, c in enumerate(bars)]
    window = None
    for i in range(pt.ST_MIN_BARS, len(bars)):
        if pt.supertrend_signal(bars[:i + 1], tf)[0] == 1:
            window = bars[:i + 1]
            break
    assert window is not None
    rep = pt.evaluate("SUPERTREND", "XAUUSD", window, None, _ticker(window[-1].close), tf=tf)
    assert rep.timeframe == tf and rep.setup_name == pt.setup_st(tf) and f" {tf}:" in rep.setup_name
    atr = dt.atr_series(window)[-1]
    assert abs((rep.suggested_entry - rep.suggested_sl) - stop * atr) < 0.02


@pytest.mark.parametrize("tf,hours,stop,trail", [("H1", 1, 3.0, 4.0), ("M15", 0.25, 3.0, 4.0), ("H4", 4, 2.0, 3.0)])
def test_pullback_uses_the_profile_of_its_timeframe(tf, hours, stop, trail):
    bars = _pullback_bars(+1, hours=hours, tf=tf)
    rep = pt.evaluate("PULLBACK", "XAUUSD", bars, UP_DAYS, _ticker(bars[-1].close), tf=tf)
    assert rep.decision == TradeSide.BUY and rep.timeframe == tf and rep.setup_name == pt.setup_pb(tf)
    atr = dt.atr_series(bars)[-1]
    assert abs((rep.suggested_entry - rep.suggested_sl) - stop * atr) < 0.02
    up = _from_closes([2000.0 + i for i in range(80)], hours=hours, tf=tf)
    act = pt.manage_action("PULLBACK", up, True, 1500.0, tf)
    assert act[1] == pytest.approx(up[-1].close - trail * dt.atr_series(up)[-1])


def test_pipeline_trades_the_timeframe_the_user_selected(market):
    trade_manager.risk_settings.portfolio_pullback_tf = "H1"
    h1 = _pullback_bars(+1, hours=1, tf="H1")
    m = market(_Market(_pullback_bars(+1), UP_DAYS, h1=h1, ticker_ts=h1[-1].timestamp + timedelta(hours=1, minutes=1)))
    res = trade_manager._portfolio_pipeline("XAUUSD")
    assert res["success"], res["reason"]
    trade = list(trade_manager.active_trades.values())[0]
    assert trade.timeframe == "H1" and trade.setup_name == pt.setup_pb("H1")
    stop = abs(trade.entry_price - trade.sl_price)
    assert stop == pytest.approx(3.0 * dt.atr_series(h1)[-1], abs=0.02)        # H1 profile: 3 x ATR


def test_open_trade_keeps_managing_on_its_own_timeframe_after_setting_change(market, monkeypatch):
    trade_manager.risk_settings.portfolio_pullback_tf = "H4"                  # user switched to H4 afterwards
    asked = []
    h1 = _from_closes([2000.0 + i for i in range(120)], hours=1, tf="H1")
    class M(_Market):
        def fetch_candles(self, symbol, tf, count=200, *a, **k):
            asked.append(tf)
            return h1 if tf == "H1" else super().fetch_candles(symbol, tf, count)
    market(M(_pullback_bars(+1), UP_DAYS))
    trade = TradeRecord(trade_id="7", symbol="XAUUSD", side=TradeSide.BUY, order_type=OrderType.MARKET,
                        entry_price=2050.0, sl_price=2040.0, tp_price=2500.0, lots=0.01, state=TradeState.OPEN,
                        open_time=datetime.now(), setup_name=pt.setup_pb("H1"), timeframe="H1")
    monkeypatch.setattr(mt5_adapter, "update_sl_tp", lambda tid, new_sl=None, new_tp=None: True)
    trade_manager._manage_portfolio_position("7", trade, True, 2200.0, 0.1, 2, {})
    assert asked == ["H1"]
    atr = dt.atr_series(h1)[-1]
    assert trade.sl_price == pytest.approx(round(h1[-1].close - 4.0 * atr, 2), abs=0.05)     # H1 trail = 4 x ATR


def test_save_endpoint_refuses_unsupported_timeframe():
    from app.api.v1.endpoints import update_risk_settings
    res = update_risk_settings(RiskSettings(strategy_profile="PORTFOLIO_TREND", max_open_positions=3, portfolio_donchian_tf="H4"))
    assert not res["success"] and "cannot run on" in res["message"]
    res = update_risk_settings(RiskSettings(strategy_profile="PORTFOLIO_TREND", max_open_positions=3,
                                            portfolio_donchian_tf="M15", donchian_channel_hours=100, donchian_trend_sma=200,
                                            portfolio_supertrend_tf="H1", portfolio_pullback_tf="M15"))
    assert res["success"], res["message"]
    assert trade_manager.risk_settings.portfolio_supertrend_tf == "H1"
