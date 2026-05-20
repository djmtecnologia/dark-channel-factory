import os
from dotenv import load_dotenv
from shared.llm_client import get_llm_client
from agents.designer.thumbnail_generator import orchestrate_thumbnail_generation

load_dotenv()

print("🔍 Initializing LLM Client...")
client = get_llm_client()

print(f"OpenAI Client Initialized: {client._openai_client is not None}")
print(f"Gemini Client Initialized: {client._gemini_client is not None}")

test_prompt = "A cinematic, realistic documentary photograph of an AI robot sitting in a dark room full of glowing server racks, extreme shock micro-expression, teal and orange color grading, 8k resolution, no text, no letters."

print("\n🖼️ Testing DALL-E 3 image generation directly...")
try:
    image_bytes = client.generate_image(test_prompt)
    if image_bytes:
        out_path = "scratch/test_dalle.png"
        with open(out_path, "wb") as f:
            f.write(image_bytes)
        print(f"✅ Success! Saved direct DALL-E 3 image to {out_path} ({len(image_bytes)} bytes)")
    else:
        print("❌ DALL-E 3 direct generation returned None.")
except Exception as e:
    print(f"❌ DALL-E 3 direct generation failed with exception: {e}")

print("\n🖼️ Testing unified thumbnail generator (orchestrate_thumbnail_generation)...")
try:
    from uuid import uuid4
    res = orchestrate_thumbnail_generation(
        job_id=uuid4(),
        title="I Replaced My Entire Team With a Single API in 2026",
        language="es-ES",
        niche="AI Automation",
        video_file_path=None,
        is_short=False
    )
    print("✅ Success! Thumbnail orchestrator finished execution.")
    print("Results summary:")
    print(f" - Region: {res.get('region')}")
    print(f" - Language: {res.get('language')}")
    print(f" - Fallback Used: {res.get('fallback_used')}")
    selected = res.get('selected_thumbnail', {})
    print(f" - Selected Variant: {selected.get('variant')}")
    print(f" - API Used: {selected.get('api_used')}")
    print(f" - Path: {selected.get('path')}")
except Exception as e:
    print(f"❌ Thumbnail orchestrator failed: {e}")
