import os
import base64
import requests
import sys
from dotenv import load_dotenv

# Set stdout to UTF-8
sys.stdout.reconfigure(encoding='utf-8')

load_dotenv()

gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
print(f"Gemini API Key: {gemini_key[:10]}...")

url = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"imagen-4.0-generate-001:predict?key={gemini_key}"
)

# According to standard Gemini Imagen API payload:
payload = {
    "instances": [{"prompt": "A professional cinematic dark YouTube thumbnail 16:9, extremely vivid colors, futuristic AI robot, neon glow, high contrast"}],
    "parameters": {"sampleCount": 1, "aspectRatio": "16:9", "outputMimeType": "image/jpeg"}
}

try:
    print("Sending request to Gemini Imagen 4.0...")
    resp = requests.post(url, json=payload, timeout=30)
    print(f"Status Code: {resp.status_code}")
    if resp.status_code == 200:
        predictions = resp.json().get("predictions", [])
        if predictions and "bytesBase64Encoded" in predictions[0]:
            img_bytes = base64.b64decode(predictions[0]["bytesBase64Encoded"])
            out_path = "scratch/test_imagen_output.jpg"
            with open(out_path, "wb") as f:
                f.write(img_bytes)
            print(f"Success! Saved Gemini Imagen 4.0 output to {out_path} ({len(img_bytes)} bytes)")
        else:
            print("No image data in predictions")
    else:
        print(f"Failed: {resp.text}")
except Exception as e:
    print(f"Exception: {e}")
