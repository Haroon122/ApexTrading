"""Phone notifications through ntfy.sh (trade opened / closed / spread warning).

The user installs the free "ntfy" phone app, subscribes to a private topic name and types the same
topic in Risk Settings. No account, token or chat ID is needed.

Sending never blocks or breaks trading: it runs on a background thread and every error is only logged.
The topic is stored in backend/data/notifier_credentials.json (git-ignored, not in risk_settings.json).
"""
import json
import logging
import os
import re
import threading
import time
import urllib.request
from typing import Any, Dict, Optional, Set

logger = logging.getLogger(__name__)

NTFY_SERVER = "https://ntfy.sh"
CREDS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                          "data", "notifier_credentials.json")
TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

OZ_PER_LOT = 100.0           # gold: 100 oz per lot
SPREAD_ALERT_COOLDOWN_S = 1800


class Notifier:
    def __init__(self):
        self._sent: Set[str] = set()
        self._last_spread_alert = 0.0
        self._lock = threading.Lock()
        self.last_error: Optional[str] = None
        self.sent_count = 0

    # ── configuration ──
    @staticmethod
    def _topic() -> str:
        try:
            with open(CREDS_PATH, encoding="utf-8") as f:
                return str(json.load(f).get("topic") or "")
        except (OSError, ValueError):
            return ""

    def configured(self) -> bool:
        return bool(self._topic())

    @staticmethod
    def enabled(rs: Any) -> bool:
        return bool(getattr(rs, "notifications_enabled", True))

    @staticmethod
    def validate(topic: str) -> Optional[str]:
        """Plain-language reason the topic is wrong, or None."""
        if not TOPIC_RE.match(topic or ""):
            return "Topic must be 8-64 letters, numbers, - or _ (no spaces). Press 'Make a secret topic' for a good one."
        return None

    def save_topic(self, topic: str) -> Optional[str]:
        topic = (topic or "").strip()
        err = self.validate(topic)
        if err:
            return err
        os.makedirs(os.path.dirname(CREDS_PATH), exist_ok=True)
        tmp = CREDS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"topic": topic}, f)
        os.replace(tmp, CREDS_PATH)
        return None

    # ── low level ──
    def _post(self, text: str, title: str = "ApexTrader", priority: int = 3, tags: str = "") -> bool:
        """Blocking HTTP call to ntfy. Returns True on success."""
        topic = self._topic()
        if not topic:
            self.last_error = "No topic set"
            return False
        headers = {"Title": title.encode("ascii", "ignore").decode(), "Priority": str(priority)}
        if tags:
            headers["Tags"] = tags
        req = urllib.request.Request(f"{NTFY_SERVER}/{topic}", data=text[:4000].encode("utf-8"),
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                ok = 200 <= resp.status < 300
            self.last_error = None if ok else f"HTTP {resp.status}"
            if ok:
                self.sent_count += 1
            return ok
        except Exception as e:                      # keep the topic out of the log/error text
            code = getattr(e, "code", None)
            self.last_error = (f"ntfy rejected the message (HTTP {code})" if code
                               else "Could not reach ntfy.sh (is this network blocking it?)")
            logger.warning(f"[NOTIFIER] send failed: {type(e).__name__} {code or ''}")
            return False

    def send(self, text: str, key: Optional[str] = None, rs: Any = None, **kw) -> bool:
        """Fire-and-forget. `key` makes a message one-shot (a trade is announced once)."""
        if not self.configured() or (rs is not None and not self.enabled(rs)):
            return False
        if key:
            with self._lock:
                if key in self._sent:
                    return False
                self._sent.add(key)
        threading.Thread(target=self._post, args=(text,), kwargs=kw, daemon=True).start()
        return True

    def send_now(self, text: str) -> bool:
        """Synchronous (used by the test button so the user sees the real result)."""
        if not self.configured():
            self.last_error = "Phone alerts are not set up yet: enter a topic in Risk Settings."
            return False
        return self._post(text, title="ApexTrader test", tags="white_check_mark")

    # ── events ──
    @staticmethod
    def _money(v: float) -> str:
        return f"{'+' if v >= 0 else '-'}${abs(v):,.2f}"

    def trade_opened(self, trade: Any, setup: str, timeframe: str, rs: Any = None) -> bool:
        try:
            side = getattr(trade.side, "value", str(trade.side))
            risk = abs(trade.entry_price - (trade.sl_price or trade.entry_price)) * trade.lots * OZ_PER_LOT
            text = (f"{side} {trade.symbol} {trade.lots} lot\n"
                    f"Strategy: {setup} ({timeframe})\n"
                    f"Entry: {trade.entry_price:.2f}\n"
                    f"Stop loss: {trade.sl_price:.2f}  (risk about ${risk:,.2f})\n"
                    f"Take profit: {trade.tp_price:.2f} (far safety target, the bot exits by trailing stop)\n"
                    f"Ticket: {trade.trade_id}")
            return self.send(text, key=f"open:{trade.trade_id}", rs=rs, title=f"Trade opened: {side}",
                             priority=4, tags="chart_with_upwards_trend")
        except Exception as e:
            logger.warning(f"[NOTIFIER] trade_opened failed: {e}")
            return False

    def trade_closed(self, trade: Any, exit_price: float, pnl_usd: float, reason: str, rs: Any = None) -> bool:
        try:
            side = getattr(trade.side, "value", str(trade.side))
            mark = "PROFIT" if pnl_usd > 0 else "LOSS" if pnl_usd < 0 else "BREAKEVEN"
            text = (f"{mark} {self._money(pnl_usd)}\n"
                    f"{side} {trade.symbol} {trade.lots} lot: {trade.entry_price:.2f} -> {exit_price:.2f}\n"
                    f"Strategy: {getattr(trade, 'setup_name', '') or '-'}\n"
                    f"Reason: {reason or '-'}\n"
                    f"Ticket: {trade.trade_id}")
            return self.send(text, key=f"close:{trade.trade_id}", rs=rs,
                             title=f"Trade closed: {mark} {self._money(pnl_usd)}",
                             priority=4 if pnl_usd < 0 else 3, tags="x" if pnl_usd < 0 else "white_check_mark")
        except Exception as e:
            logger.warning(f"[NOTIFIER] trade_closed failed: {e}")
            return False

    def check_spread(self, ticker: Any, open_positions: int, rs: Any) -> bool:
        """Warn when the spread explodes while a trade is open (e.g. the daily market reopen)."""
        try:
            limit = float(getattr(rs, "notify_spread_alert_usd", 5.0) or 0.0)
            if limit <= 0 or open_positions <= 0 or ticker is None:
                return False
            spread = float(ticker.ask) - float(ticker.bid)
            if spread < limit or time.time() - self._last_spread_alert < SPREAD_ALERT_COOLDOWN_S:
                return False
            self._last_spread_alert = time.time()
            return self.send(f"{ticker.symbol} spread is ${spread:,.2f} (bid {ticker.bid}, ask {ticker.ask}).\n"
                             f"You have {open_positions} open trade(s). A SELL stop-loss can be triggered by a wide ask.",
                             rs=rs, title="Spread warning", priority=5, tags="warning")
        except Exception as e:
            logger.warning(f"[NOTIFIER] check_spread failed: {e}")
            return False

    def status(self, rs: Any = None) -> Dict[str, Any]:
        topic = self._topic()
        return {"configured": bool(topic), "enabled": self.enabled(rs) if rs is not None else True,
                "topic_hint": f"...{topic[-4:]}" if topic else "",
                "sent": self.sent_count, "last_error": self.last_error}


notifier = Notifier()
