import urllib.request
import urllib.parse
import json

def api_request(path, method="GET", params=None, data=None):
    url = f"http://localhost:8000{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    
    req = urllib.request.Request(url, method=method)
    req.add_header("X-API-Key", "apex_secret_api_key_2026")
    req.add_header("Content-Type", "application/json")
    
    if data:
        json_data = json.dumps(data).encode("utf-8")
        req.data = json_data
        
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as e:
        print(f"Error requesting {url}: {e}")
        if hasattr(e, 'read'):
            print("Response body:", e.read().decode("utf-8"))
        return None

def main():
    print("Checking live server root...")
    root = api_request("/")
    print("Root response:", root)
    if not root:
        print("Live server is not reachable on http://localhost:8000")
        return
        
    print("\nChecking bot status...")
    status = api_request("/api/v1/bot/status")
    print("Status response:", status)
    
    print("\nStarting bot (with params)...")
    start = api_request("/api/v1/bot/start", method="POST", params={"symbol": "EURUSD", "timeframe": "M15"})
    print("Start response:", start)
    
    print("\nChecking bot status after start...")
    status_after = api_request("/api/v1/bot/status")
    print("Status after start:", status_after)
    
    if status_after and status_after.get("engine_state") == "ERROR":
        print("\nEngine is in ERROR state! Stopping bot to reset...")
        stop = api_request("/api/v1/bot/stop", method="POST")
        print("Stop response:", stop)

if __name__ == "__main__":
    main()
