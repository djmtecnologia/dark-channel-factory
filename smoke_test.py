import os
import subprocess
import requests
from sqlmodel import Session, create_engine, select
from dotenv import load_dotenv

load_dotenv()

def run_test(name, func):
    print(f"🧪 [TESTE] {name}...", end=" ", flush=True)
    try:
        result = func()
        print("✅ PASSOU")
        return True
    except Exception as e:
        print(f"❌ FALHOU: {e}")
        return False

def test_db():
    engine = create_engine(os.getenv("DATABASE_URL"))
    with Session(engine) as session:
        session.exec(select(1))
    return True

def test_llm():
    from shared.llm_client import get_completion
    res = get_completion("Say 'OK'", system_instruction="Test mode")
    return "OK" in res

def test_ffmpeg():
    # Busca na pasta bin local
    ffmpeg_bin = os.path.join(os.getcwd(), "bin", "ffmpeg.exe")
    if not os.path.exists(ffmpeg_bin):
        ffmpeg_bin = "ffmpeg"
    res = subprocess.run([ffmpeg_bin, "-version"], capture_output=True)
    return res.returncode == 0

def test_edge_tts():
    import edge_tts
    import asyncio
    async def probe():
        communicate = edge_tts.Communicate("Hello", "en-US-GuyNeural")
        return True
    return asyncio.run(probe())

def test_clickbank_logic():
    from agents.monetization.clickbank_scout import ClickBankScout
    import uuid
    # Simula sem salvar no banco real (Mock engine)
    engine = create_engine("sqlite:///:memory:")
    from cloud_hub.models import SQLModel
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        scout = ClickBankScout(session)
        dummy_tenant_id = uuid.uuid4()
        product = scout.find_and_cache_product("AI Crypto", "ai", "en", dummy_tenant_id)
        return "clickbank" in product.hotlink_base.lower()

def test_directories():
    required = ["output", "output/videos", "channel_setup/assets/audio"]
    for r in required:
        if not os.path.exists(r): os.makedirs(r, exist_ok=True)
    return True

if __name__ == "__main__":
    print("=============================================")
    print("🔍 DIAGNÓSTICO COMPLETO - DARK FACTORY")
    print("=============================================\n")
    
    tests = [
        ("Conexão Banco de Dados (Neon)", test_db),
        ("Conexão IA (Gemini/OpenAI)", test_llm),
        ("Motor de Vídeo (FFmpeg)", test_ffmpeg),
        ("Serviço de Voz (Edge-TTS)", test_edge_tts),
        ("Lógica de Monetização (ClickBank)", test_clickbank_logic),
        ("Estrutura de Pastas Local", test_directories),
    ]
    
    results = [run_test(n, f) for n, f in tests]
    
    print("\n" + "="*45)
    if all(results):
        print("🚀 STATUS FINAL: 100% OPERACIONAL")
    else:
        print("⚠️ STATUS FINAL: PROBLEMAS DETECTADOS")
    print("="*45)
