"""
Economic Calendar & News Event Filter Module
Prevents opening trades during high-impact news releases (NFP, CPI, FOMC, Rate Decisions)
Separates LIVE vs MOCK providers and ensures mock news data never silently blocks live trading.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple
import logging
import urllib.request
import json as _json

logger = logging.getLogger("ApexTrader.NewsFilter")

# ---------------------------------------------------------------------------
# Rate-limit constants for the Forex Factory calendar feed
# ---------------------------------------------------------------------------
_FF_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
_FF_CACHE_TTL_SECONDS = 1800  # 30 minutes — well within the 2 req/5 min limit
_FF_REQUEST_TIMEOUT_SECONDS = 10


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


class LiveCalendarProvider(EconomicCalendarProvider):
    """
    Live economic calendar provider using the Forex Factory weekly JSON feed.

    Fetches from https://nfs.faireconomy.media/ff_calendar_thisweek.json at most
    once every _FF_CACHE_TTL_SECONDS (default 30 min) and caches results in memory.
    The feed allows only 2 requests per 5 minutes; if rate-limited it returns an
    HTML "Request Denied" page — we detect that and fall back to the last valid
    cache rather than crashing.

    Parsed events use the same dict shape as MockCalendarProvider:
        {"name": str, "currency": str, "timestamp": datetime, "impact": "HIGH"}

    Only HIGH-impact events are retained, matching the downstream filter logic.
    """

    def __init__(self):
        self._cached_events: Optional[List[Dict[str, Any]]] = None
        self._cache_timestamp: Optional[datetime] = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_events(
        self, start: datetime, end: datetime
    ) -> Tuple[Optional[List[Dict[str, Any]]], bool]:
        """
        Returns (events, is_available).
        Events are filtered to those whose timestamp falls between start and end.
        If the live fetch fails and no cache exists yet, returns (None, False)
        so the caller treats the feed as unavailable rather than crashing.
        """
        events = self._get_or_refresh_events()
        if events is None:
            # No cache at all and live fetch failed — signal unavailability
            return None, False

        # Filter to the requested time window
        window = [e for e in events if start <= e["timestamp"] <= end]
        return window, True

    # ------------------------------------------------------------------
    # Internal cache / fetch helpers
    # ------------------------------------------------------------------

    def _get_or_refresh_events(self) -> Optional[List[Dict[str, Any]]]:
        """Return cached events if fresh, otherwise attempt a live fetch."""
        now = datetime.now()
        cache_age = (
            (now - self._cache_timestamp).total_seconds()
            if self._cache_timestamp is not None
            else float("inf")
        )

        if cache_age < _FF_CACHE_TTL_SECONDS and self._cached_events is not None:
            return self._cached_events  # Cache still valid

        # Attempt live fetch — safe display even when cache_age is infinity (first run)
        cache_age_display = "never cached" if cache_age == float("inf") else f"{int(cache_age)}s"
        logger.info(
            "[NEWS FILTER] Fetching fresh economic calendar from FF feed "
            f"(cache age: {cache_age_display}, TTL: {_FF_CACHE_TTL_SECONDS}s)"
        )
        try:
            fresh = self._fetch_and_parse()
            if fresh is not None:
                self._cached_events = fresh
                self._cache_timestamp = now
                logger.info(
                    f"[NEWS FILTER] Live calendar fetched OK: "
                    f"{len(fresh)} high-impact events cached."
                )
                return fresh
        except Exception as exc:
            logger.warning(
                f"[NEWS FILTER] Live calendar fetch raised unexpected error: {exc}. "
                "Falling back to cached data."
            )

        # Fetch failed — use stale cache if available, else None
        if self._cached_events is not None:
            logger.warning(
                "[NEWS FILTER] Using stale cached calendar data "
                f"(age: {cache_age_display})."
            )
            return self._cached_events

        logger.warning(
            "[NEWS FILTER] No cached calendar data available and live fetch failed. "
            "News filter will return UNKNOWN status."
        )
        return None

    def _fetch_and_parse(self) -> Optional[List[Dict[str, Any]]]:
        """
        Perform a single HTTP GET to the FF calendar endpoint.
        Returns a list of parsed HIGH-impact event dicts, or None if the
        response is not valid JSON (e.g. HTML rate-limit page or HTTP error).
        """
        import urllib.error as _urlerr

        req = urllib.request.Request(
            _FF_CALENDAR_URL,
            headers={
                "User-Agent": "ApexTrader-NewsFilter/1.0",
                "Accept": "application/json",
            },
        )
        try:
            resp_ctx = urllib.request.urlopen(req, timeout=_FF_REQUEST_TIMEOUT_SECONDS)
        except _urlerr.HTTPError as http_exc:
            if http_exc.code == 429:
                logger.warning(
                    "[NEWS FILTER] FF calendar rate-limited (HTTP 429 Too Many Requests). "
                    "Will use cached data until next TTL window."
                )
            else:
                logger.warning(
                    f"[NEWS FILTER] FF calendar HTTP error {http_exc.code}: {http_exc.reason}. "
                    "Will use cached data."
                )
            return None
        except _urlerr.URLError as url_exc:
            logger.warning(
                f"[NEWS FILTER] FF calendar network error: {url_exc.reason}. "
                "Will use cached data."
            )
            return None

        with resp_ctx as resp:
            content_type = resp.headers.get("Content-Type", "")
            raw = resp.read().decode("utf-8", errors="replace")

        # Detect HTML "Request Denied" pages before attempting JSON parse
        if "text/html" in content_type or raw.lstrip().startswith("<"):
            logger.warning(
                "[NEWS FILTER] FF calendar returned HTML instead of JSON "
                "(likely rate-limited or access denied). Will use cached data."
            )
            return None

        try:
            payload = _json.loads(raw)
        except _json.JSONDecodeError as exc:
            logger.warning(
                f"[NEWS FILTER] FF calendar JSON parse error: {exc}. "
                "Will use cached data."
            )
            return None

        if not isinstance(payload, list):
            logger.warning(
                "[NEWS FILTER] FF calendar response is not a list — "
                f"type={type(payload)}. Will use cached data."
            )
            return None

        events = []
        for item in payload:
            try:
                impact_raw = item.get("impact", "")  # "High" / "Medium" / "Low"
                if impact_raw.lower() != "high":
                    continue  # Only retain HIGH-impact events

                title = item.get("title", "Unknown Event")
                currency = item.get("country", "")  # FF uses "country" for currency
                date_str = item.get("date", "")

                # Parse ISO-8601 timestamp (may include timezone offset like -04:00)
                ts = self._parse_ff_datetime(date_str)
                if ts is None:
                    continue

                events.append(
                    {
                        "name": title,
                        "currency": currency,
                        "timestamp": ts,
                        "impact": "HIGH",  # Normalise to uppercase for downstream
                    }
                )
            except Exception as parse_exc:
                logger.debug(
                    f"[NEWS FILTER] Skipping malformed event entry: {parse_exc}"
                )

        return events

    @staticmethod
    def _parse_ff_datetime(date_str: str) -> Optional[datetime]:
        """
        Parse the FF calendar date string (ISO-8601 with offset, e.g.
        "2026-08-28T10:00:00-04:00") into a naive UTC-equivalent datetime.
        Returns None if parsing fails.
        """
        if not date_str:
            return None
        try:
            # Python 3.7+ fromisoformat handles offset-aware strings
            dt_aware = datetime.fromisoformat(date_str)
            # Convert to naive UTC
            dt_utc = dt_aware.astimezone(timezone.utc).replace(tzinfo=None)
            return dt_utc
        except Exception:
            # Fallback: strip offset and parse as local time (best-effort)
            try:
                trimmed = date_str[:19]  # "2026-08-28T10:00:00"
                return datetime.strptime(trimmed, "%Y-%m-%dT%H:%M:%S")
            except Exception:
                return None


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


# ---------------------------------------------------------------------------
# Global news_filter instance — wired with the live FF calendar provider.
# The live provider can be disabled via the LIVE_NEWS_CALENDAR_ENABLED flag
# in config.py or by setting live_news_calendar_enabled=False in risk_settings
# without any code changes.
# ---------------------------------------------------------------------------
try:
    from app.core.config import settings as _settings
    _live_news_enabled = getattr(_settings, "LIVE_NEWS_CALENDAR_ENABLED", True)
except Exception:
    _live_news_enabled = True

news_filter = NewsEventFilter()
if _live_news_enabled:
    news_filter.provider = LiveCalendarProvider()
    logger.info(
        "[NEWS FILTER] LiveCalendarProvider wired — live economic calendar active."
    )
else:
    logger.warning(
        "[NEWS FILTER] LIVE_NEWS_CALENDAR_ENABLED=False in config — "
        "live calendar provider is disabled. News filter will return UNKNOWN."
    )
