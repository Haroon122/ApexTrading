"""
Signal Evaluation History & Rejection Histogram Tracker Module.
Records all decision engine evaluations and tracks rejection reasons.
"""

import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from collections import Counter

logger = logging.getLogger("ApexTrader.SignalHistory")


class SignalHistoryTracker:
    """
    Stores and analyzes decision evaluation history.
    Produces rejection histograms and performance metrics.
    """

    def __init__(self, max_history: int = 2000):
        self.max_history = max_history
        self.history: List[Dict[str, Any]] = []

    def record_evaluation(
        self,
        symbol: str,
        direction: str,
        best_setup: str,
        regime: str,
        buy_score: float,
        sell_score: float,
        confidence: float,
        spread_pips: float,
        rr_ratio: float,
        news_status: str,
        decision: str,
        primary_reason: str,
        secondary_reasons: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        entry = {
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "direction": direction,
            "best_setup": best_setup,
            "regime": regime,
            "buy_score": round(buy_score, 4),
            "sell_score": round(sell_score, 4),
            "confidence": round(confidence, 4),
            "spread_pips": round(spread_pips, 1),
            "rr_ratio": round(rr_ratio, 2),
            "news_status": news_status,
            "decision": decision,
            "primary_reason": primary_reason,
            "secondary_reasons": secondary_reasons or []
        }

        self.history.append(entry)
        if len(self.history) > self.max_history:
            self.history.pop(0)

        return entry

    def get_rejection_histogram(self) -> Dict[str, int]:
        reasons = [e["primary_reason"] for e in self.history if e["decision"] == "HOLD" or e["primary_reason"] != "NONE"]
        return dict(Counter(reasons))

    def get_summary_stats(self) -> Dict[str, Any]:
        total = len(self.history)
        if total == 0:
            return {
                "total_evaluations": 0,
                "buy_signals": 0,
                "sell_signals": 0,
                "hold_evaluations": 0,
                "rejection_histogram": {}
            }

        buys = sum(1 for e in self.history if e["decision"] == "BUY")
        sells = sum(1 for e in self.history if e["decision"] == "SELL")
        holds = sum(1 for e in self.history if e["decision"] == "HOLD")

        return {
            "total_evaluations": total,
            "buy_signals": buys,
            "sell_signals": sells,
            "hold_evaluations": holds,
            "rejection_histogram": self.get_rejection_histogram()
        }

    def clear(self):
        self.history.clear()


signal_history = SignalHistoryTracker()
