import os
import json
import urllib.request
import urllib.error

key = None
if os.path.exists(".env"):
    with open(".env", "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("GEMINI_API_KEY="):
                key = line.split("=", 1)[1].strip()
                if (key.startswith('"') and key.endswith('"')) or (key.startswith("'") and key.endswith("'")):
                    key = key[1:-1]
                break

if not key:
    print("❌ GEMINI_API_KEY not found in .env file.")
    exit(1)

print(f"Listing models for API Key: {key[:8]}...{key[-8:]}")

# Test with v1beta and v1
for api_version in ["v1beta", "v1"]:
    url = f"https://generativelanguage.googleapis.com/{api_version}/models?key={key}"
    print(f"\n--- Testing API Version: {api_version} ---")
    
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode("utf-8"))
            print(f"✅ Success! Found {len(data.get('models', []))} models:")
            for m in data.get("models", []):
                print(f" - {m.get('name')} (supported: {m.get('supportedGenerationMethods')})")
    except urllib.error.HTTPError as e:
        status = e.code
        body = e.read().decode("utf-8")
        print(f"❌ HTTP Error {status} on {api_version}:")
        print(body)
    except Exception as e:
        print(f"❌ Failed: {e}")
