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

print(f"Testing active models with key: {key[:8]}...{key[-8:]}")

models = ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-3.5-flash", "gemini-3.1-flash-lite"]

for model in models:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    print(f"\n--- Testing model: {model} ---")
    
    payload = {
        "contents": [{"parts": [{"text": "Hello, write a 3-word joke."}]}]
    }
    data = json.dumps(payload).encode("utf-8")
    
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req) as response:
            status = response.status
            body = response.read().decode("utf-8")
            data_resp = json.loads(body)
            text = data_resp['candidates'][0]['content']['parts'][0]['text'].strip()
            print(f"✅ SUCCESS! Model: {model}")
            print(f"Response: {text}")
    except urllib.error.HTTPError as e:
        status = e.code
        body = e.read().decode("utf-8")
        print(f"❌ FAILED! Model: {model} (HTTP Error {status})")
        print(body[:200])
    except Exception as e:
        print(f"❌ Request failed for {model}: {e}")
