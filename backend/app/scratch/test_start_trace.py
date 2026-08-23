import sys
import os

# Add backend root to python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from fastapi.testclient import TestClient
from app.main import app

def main():
    client = TestClient(app)
    headers = {"X-API-Key": "apex_secret_api_key_2026"}
    
    print("Calling POST /api/v1/bot/start via TestClient...")
    try:
        response = client.post("/api/v1/bot/start", headers=headers)
        print("Status code:", response.status_code)
        print("Response json:", response.json())
    except Exception as e:
        print("Exception occurred:")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
