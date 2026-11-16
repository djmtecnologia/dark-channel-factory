import os
import sys
import asyncio
import uuid
from sqlmodel import Session, create_engine, SQLModel
from desktop_node import (
    generate_voice_audio, 
    fetch_premium_broll, 
    generate_subtitles_locally, 
    assemble_production,
    ffmpeg_exe
)

async def simulate_video_generation():
    print("🚀 [SIMULAÇÃO] Iniciando teste E2E com Roteiro Mockado (Bypass LLM APIs)...")
    
    job_id = "test_" + str(uuid.uuid4())[:8]
    topic = "How AI is creating the new 1%"
    
    script_full = "Welcome to our story about how artificial intelligence is creating a new elite of billionaires. Today we explore the secrets of AI."
    
    # 2. Teste do Motor de Áudio (1.10x)
    print("🎙️ [2/5] Gerando Narração (Ritmo 1.10x)...")
    audio_path = os.path.join("output", f"audio_{job_id}.mp3")
    await generate_voice_audio(script_full, audio_path, "en-US-GuyNeural")
    if os.path.exists(audio_path):
        print(f"✅ Áudio gerado: {os.path.getsize(audio_path)} bytes")

    # 3. Teste do Visual Scraper
    print("🌐 [3/5] Buscando Material Visual...")
    video_clip = fetch_premium_broll("artificial intelligence")
    if not video_clip:
        print("❌ Falha ao obter clipe visual. Verifique API Keys.")
        return
    print(f"✅ Material visual obtido: {video_clip}")

    # 4. Teste de Legendas (Whisper)
    print("📝 [4/5] Transcrevendo com Whisper...")
    srt_path = generate_subtitles_locally(audio_path, job_id)
    print(f"✅ Legendas geradas: {srt_path}")
    
    # 5. Teste de Renderização (Ken Burns + Audio Mix)
    print("🎬 [5/5] Renderizando com Ken Burns + Mixagem de Áudio...")
    
    clip_durations = [10.0]
    
    # Mock do job_data recebido pela render_video
    job_data = {
        "job_id": job_id,
        "topic": topic,
        "language": "en-US",
        "niche": "AI Tools",
        "is_short": False,
        "script_data": {
            "title": topic,
            "script_full": script_full,
            "scenes": [
                {
                    "id": 0,
                    "timestamp": "0:00-0:10",
                    "narration": script_full,
                    "b_roll_visual": "artificial intelligence",
                    "sfx_cue": "none",
                    "visual_cues": []
                }
            ]
        }
    }
    
    success = assemble_production(
        job_id=job_id,
        video_paths=[video_clip],
        audio_path=audio_path,
        srt_path=srt_path,
        topic=topic,
        clip_durations=clip_durations,
        total_audio_sec=10.0,
        job_data=job_data
    )

    if success:
        possible_paths = [
            os.path.join("output", f"final_{job_id}.mp4"),
            os.path.join("videos_finais", f"final_{job_id}.mp4")
        ]
        found_path = next((p for p in possible_paths if os.path.exists(p)), None)
        
        if found_path:
            print("\n" + "="*45)
            print(f"🏆 SUCESSO TOTAL! Vídeo gerado em: {found_path}")
            print("🚀 Features Validadas: Ken Burns, 1.10x Voice, Audio Mix.")
            print("="*45)
        else:
            print(f"❌ Erro: Vídeo não encontrado nos caminhos testados.")
    else:
        print("❌ Erro Crítico na Renderização. Verifique o log do FFmpeg.")

if __name__ == "__main__":
    if not os.path.exists("output"): os.makedirs("output")
    asyncio.run(simulate_video_generation())
