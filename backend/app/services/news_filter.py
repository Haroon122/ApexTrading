"""
Economic Calendar & News Event Filter Module
Prevents opening trades during high-impact news releases (NFP, CPI, FOMC, Rate Decisions)
Separates LIVE vs MOCK providers and ensures mock news data never silently blocks live trading.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
import logging

logger = logging.getLogger("ApexTrader.NewsFilter")


class EconomicCalendarProvider(ABC):
    """Abstract Provider Interface for Economic Calendar events."""
    @abstractmethod
    def get_events(self, start: datetime, end: datetime) -> Tuple[Optional[List[Dict[str, Any]]], bool]:
        """
        Retrieves calendar events between start and end.
        Returns (events_list, is_available).
        """
        pass


class MockCalendarProvider(EconomicCalendarProvider):
    """
    Placeholder economic calendar provider for testing.
    Constructs high-impact events dynamically based on weekday schedule.
    """
    def __init__(self, simulate_failure: bool = False):
        self.simulate_failure = simulate_failure

    def get_events(self, start: datetime, end: datetime) -> Tuple[Optional[List[Dict[str, Any]]], bool]:
        if self.simulate_failure:
            return None, False

        events = []
        # Construct standard recurring high-impact events for testing
        current = start.replace(hour=0, minute=0, second=0, microsecond=0)
        while current <= end:
            if current.weekday() == 2:  # Wednesday
                events.append({
                    "name": "US CPI Inflation Data (MOCK)",
                    "currency": "USD",
                    "timestamp": current.replace(hour=13, minute=30),
                    "impact": "HIGH"
                })
                events.append({
                    "name": "FOMC Interest Rate Decision (MOCK)",
                    "currency": "USD",
                    "timestamp": current.replace(hour=19, minute=0),
                    "impact": "HIGH"
                })
            elif current.weekday() == 3:  # Thursday
                events.append({
                    "name": "ECB Interest Rate Decision (MOCK)",
                    "currency": "EUR",
                    "timestamp": current.replace(hour=12, minute=15),
                    "impact": "HIGH"
                })
            elif current.weekday() == 4:  # Friday
                events.append({
                    "name": "US Non-Farm Payrolls NFP (MOCK)",
                    "currency": "USD",
                    "timestamp": current.replace(hour=13, minute=30),
                    "impact": "HIGH"
                })
            current += timedelta(days=1)

        return events, True


class NewsEventFilter:
    """
    Economic Calendar & High-Impact Event Filter.
    Checks if a high-impact news event is scheduled within the blackout window.
    """

    def __init__(
        self,
        blackout_before_minutes: int = 30,
        blackout_after_minutes: int = 30,
        block_high_impact_news: bool = True,
        block_on_unavailable: bool = False,
        use_mock_news: bool = False
    ):
        self.blackout_before_minutes = blackout_before_minutes
        self.blackout_after_minutes = blackout_after_minutes
        self.block_high_impact_news = block_high_impact_news
        self.block_on_unavailable = block_on_unavailable
        self.use_mock_news = use_mock_news
        self.provider: Optional[EconomicCalendarProvider] = None

    def get_news_status(self) -> str:
        if not self.block_high_impact_news:
            return "DISABLED"
        if self.provider is None:
            return "MOCK" if self.use_mock_news else "UNKNOWN"
        if isinstance(self.provider, MockCalendarProvider):
            return "MOCK" if self.use_mock_news else "UNKNOWN"
        return "LIVE"

    def is_news_blackout(self, symbol: str, current_time: Optional[datetime] = None) -> Tuple[bool, str]:
        if not self.block_high_impact_news:
            return False, "News blocking is disabled in settings"

        if current_time is None:
            current_time = datetime.now()

        # If no live provider is configured and use_mock_news is False:
        if (self.provider is None or isinstance(self.provider, MockCalendarProvider)) and not self.use_mock_news:
            return False, "NEWS_STATUS: UNKNOWN (Live provider not configured; mock news protection bypassed)"

        active_provider = self.provider or MockCalendarProvider()

        start = current_time - timedelta(minutes=self.blackout_after_minutes + 10)
        end = current_time + timedelta(minutes=self.blackout_before_minutes + 10)

        events, available = active_provider.get_events(start, end)
        if not available:
            if self.block_on_unavailable:
                logger.warning("[NEWS FILTER] News calendar data is unavailable! Fail-safe block active.")
                return True, "NEWS_DATA_UNAVAILABLE"
            else:
                return False, "NEWS_STATUS: UNKNOWN (Calendar data unavailable)"

        if not events:
            return False, "No high-impact news blackout detected"

        symbol_currencies = []
        sym_up = symbol.upper()
        if "USD" in sym_up or "XAU" in sym_up or "GOLD" in sym_up:
            symbol_currencies.append("USD")
        if "EUR" in sym_up:
            symbol_currencies.append("EUR")
        if "GBP" in sym_up:
            symbol_currencies.append("GBP")

        for event in events:
            if event.get("currency") in symbol_currencies and event.get("impact") == "HIGH":
                event_time = event["timestamp"]
                diff_seconds = (event_time - current_time).total_seconds()
                
                # Check before event blackout (event_time > current_time)
                if 0 <= diff_seconds <= (self.blackout_before_minutes * 60):
                    mins = round(diff_seconds / 60.0, 1)
                    return True, f"High-Impact News Blackout: {event['name']} in {mins} minutes"
                # Check after event blackout (current_time >= event_time)
                elif 0 < -diff_seconds <= (self.blackout_after_minutes * 60):
                    mins = round(-diff_seconds / 60.0, 1)
                    return True, f"High-Impact News Blackout: {event['name']} passed {mins} minutes ago"

        return False, "No high-impact news blackout detected"


news_filter = NewsEventFilter()
