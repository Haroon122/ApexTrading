"""
Live network verification of the news filter change.
Runs a FRESH HTTP fetch (bypasses any existing in-memory cache) and reports
exact results from this machine's network connection.
No injected data. No previously-populated cache.
"""

import sys, os, time, urllib.request, json, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
SEP = "=" * 65

# ──────────────────────────────────────────────────────────────────────
# STEP 1: Raw HTTP test (completely independent of our code)
# ──────────────────────────────────────────────────────────────────────
print(SEP)
print("STEP 1: Raw HTTP GET to FF calendar endpoint")
print(SEP)
print("URL :", _FF_URL)
print()

raw_fetch_ok = False
raw_body = None
http_status = None
content_type_header = None
fetch_error = None

t0 = time.time()
try:
    req = urllib.request.Request(
        _FF_URL,
        headers={
            "User-Agent": "ApexTrader-NewsFilter/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        http_status = resp.status
        content_type_header = resp.headers.get("Content-Type", "(not set)")
        raw_body = resp.read().decode("utf-8", errors="replace")
    elapsed = time.time() - t0
    raw_fetch_ok = True
    print("HTTP status      :", http_status)
    print("Content-Type     :", content_type_header)
    print("Response size    : %d bytes" % len(raw_body))
    print("Round-trip time  : %.2f s" % elapsed)
    is_html = "text/html" in (content_type_header or "") or raw_body.lstrip().startswith("<")
    print("Looks like JSON  : %s" % (not is_html))
    if is_html:
        print("WARNING: Response appears to be HTML (rate-limited or blocked).")
        print("First 300 chars  :", raw_body[:300])
except Exception as exc:
    elapsed = time.time() - t0
    fetch_error = exc
    print("FETCH FAILED after %.2f s" % elapsed)
    print("Error type       :", type(exc).__name__)
    print("Error detail     :", exc)

# ──────────────────────────────────────────────────────────────────────
# STEP 2: Parse if fetch succeeded
# ──────────────────────────────────────────────────────────────────────
print()
print(SEP)
print("STEP 2: JSON parsing & event extraction")
print(SEP)

parsed_events = []
parse_error = None

if raw_fetch_ok and raw_body and not (raw_body.lstrip().startswith("<")):
    try:
        payload = json.loads(raw_body)
        total_events = len(payload)
        high_events = [e for e in payload if str(e.get("impact","")).lower() == "high"]
        print("Total events in feed     :", total_events)
        print("HIGH-impact events only  :", len(high_events))
        print()
        print("All HIGH-impact events this week:")
        from datetime import datetime, timezone
        for item in high_events:
            date_str = item.get("date","")
            try:
                dt_aware = datetime.fromisoformat(date_str)
                dt_utc = dt_aware.astimezone(timezone.utc).replace(tzinfo=None)
                ts_str = dt_utc.strftime("%Y-%m-%d %H:%M UTC")
            except Exception:
                ts_str = date_str + " (parse error)"
            print("  [%s] %-45s  %s" % (
                item.get("country","??"),
                item.get("title","??"),
                ts_str
            ))
            parsed_events.append(item)
    except Exception as exc:
        parse_error = exc
        print("JSON PARSE FAILED:", exc)
        print("First 300 chars of body:", raw_body[:300] if raw_body else "(empty)")
elif raw_fetch_ok:
    print("Skipped: response was HTML (not JSON).")
else:
    print("Skipped: HTTP fetch failed.")

# ──────────────────────────────────────────────────────────────────────
# STEP 3: Test LiveCalendarProvider (fresh instance, no pre-cached data)
# ──────────────────────────────────────────────────────────────────────
print()
print(SEP)
print("STEP 3: LiveCalendarProvider fresh instance (no prior cache)")
print(SEP)

from app.services.news_filter import LiveCalendarProvider, news_filter

fresh_provider = LiveCalendarProvider()
# Confirm cache is empty on a brand-new instance
assert fresh_provider._cached_events is None, "Expected no cache on new instance"
assert fresh_provider._cache_timestamp is None, "Expected no timestamp on new instance"
print("New provider cache state : empty (correct)")

t1 = time.time()
events_result = fresh_provider._get_or_refresh_events()
elapsed1 = time.time() - t1

if events_result is not None:
    print("Live fetch result        : SUCCESS")
    print("HIGH-impact events cached: %d" % len(events_result))
    print("Cache timestamp set      : %s" % fresh_provider._cache_timestamp)
    print("Fetch time               : %.2f s" % elapsed1)
    print()
    print("Parsed event list (name | currency | timestamp UTC):")
    for e in events_result:
        print("  %-50s | %-4s | %s" % (
            e["name"][:50], e["currency"],
            e["timestamp"].strftime("%Y-%m-%d %H:%M")
        ))
else:
    print("Live fetch result        : FAILED (returned None)")
    print("Fetch attempt time       : %.2f s" % elapsed1)
    print("Cache is empty           : %s" % (fresh_provider._cached_events is None))

# STEP 3b: Second call — must be instant (cache hit)
print()
print("Cache hit test (second call should be ~0 ms):")
t2 = time.time()
events_result2 = fresh_provider._get_or_refresh_events()
elapsed2 = (time.time() - t2) * 1000
print("  Second call time: %.2f ms (should be < 1 ms for cache hit)" % elapsed2)
print("  Cache hit       : %s" % (elapsed2 < 50))

# ──────────────────────────────────────────────────────────────────────
# STEP 4: Fallback behaviour when no cache exists and fetch fails
# ──────────────────────────────────────────────────────────────────────
print()
print(SEP)
print("STEP 4: Fallback safety check")
print(SEP)

# Simulate a provider that has never fetched and will always fail
class _AlwaysFailProvider(LiveCalendarProvider):
    def _fetch_and_parse(self):
        raise ConnectionError("Simulated network failure")

fail_provider = _AlwaysFailProvider()
from app.services.news_filter import NewsEventFilter

fail_filter = NewsEventFilter()
fail_filter.provider = fail_provider

from datetime import datetime
is_blocked, msg = fail_filter.is_news_blackout("XAUUSD", current_time=datetime.now())
print("Simulated fetch-fail + no cache:")
print("  is_news_blackout = %s  (must be False)" % is_blocked)
print("  message          = %s" % msg)
fallback_ok = (not is_blocked) and ("UNKNOWN" in msg or "unavailable" in msg.lower() or "not configured" in msg.lower())
print("  Fallback correct : %s" % fallback_ok)

# ──────────────────────────────────────────────────────────────────────
# STEP 5: Global news_filter status (reflects real startup state)
# ──────────────────────────────────────────────────────────────────────
print()
print(SEP)
print("STEP 5: Global news_filter instance state (module-level / startup)")
print(SEP)

global_provider = news_filter.provider
print("Provider type          : %s" % type(global_provider).__name__)
print("get_news_status()      : %s" % news_filter.get_news_status())
if hasattr(global_provider, "_cache_timestamp"):
    print("Cache timestamp        : %s" % global_provider._cache_timestamp)
    print("Cached events count    : %s" % (len(global_provider._cached_events) if global_provider._cached_events else 0))
else:
    print("(Provider has no cache attribute - not a LiveCalendarProvider)")

# ──────────────────────────────────────────────────────────────────────
# FINAL SUMMARY
# ──────────────────────────────────────────────────────────────────────
print()
print(SEP)
print("FINAL SUMMARY")
print(SEP)
print("HTTP fetch succeeded     : %s" % raw_fetch_ok)
if raw_fetch_ok:
    print("HTTP status code         : %s" % http_status)
    print("Events parsed            : %d HIGH-impact" % len(parsed_events))
else:
    print("Fetch error              : %s - %s" % (type(fetch_error).__name__, fetch_error))
print("LiveCalendarProvider     : %s" % ("LIVE OK" if events_result is not None else "FALLBACK (fetch failed, no crash)"))
print("news_filter status       : %s" % news_filter.get_news_status())
print("Fallback behaviour       : %s" % ("PASS (non-blocking UNKNOWN)" if fallback_ok else "FAIL"))
