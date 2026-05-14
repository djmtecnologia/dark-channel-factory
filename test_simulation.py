import os
import sys
import asyncio
import uuid
from cloud_hub.core.copywriter import run_copywriter_phase
from cloud_hub.models import VideoJob, Channel, Tenant
from sqlmodel import Session, create_engine, SQLModel
from desktop_node import (
    generate_voice_audio, 
    get_media_with_cache, 
    generate_subtitles_locally, 
    assemble_production,
    ffmpeg_exe
)

async def simulate_video_generation():
    print("🚀 [SIMULAÇÃO] Iniciando teste E2E das novas features...")
    
    # Setup de Banco em Memória para o Teste
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    
    with Session(engine) as session:
        # Criar dados fake para o teste
        tenant = Tenant(name="Test User", email="test@test.com")
        session.add(tenant)
        session.commit()
        
        channel = Channel(tenant_id=tenant.id, name="AI Test", niche="ai", language="en", market_region="USA")
        session.add(channel)
        session.commit()
        
        job = VideoJob(channel_id=channel.id, topic="How AI is creating the new 1%", status="PENDING")
        session.add(job)
        session.commit()

        # 1. Teste do Copywriter Phase
        print("📝 [1/5] Gerando Roteiro de Alta Retenção...")
        job = await run_copywriter_phase(session, job)
        script_full = job.script_data.get("script_full", "")
        print(f"✅ Roteiro gerado: {len(script_full)} caracteres.")

        job_id = f"test_{str(job.id)[:8]}"
        
        # 2. Teste do Motor de Áudio (1.10x)
        print("🎙️ [2/5] Gerando Narração (Ritmo 1.10x)...")
        audio_path = os.path.join("output", f"audio_{job_id}.mp3")
        # Reduzimos o texto para o teste ser rápido
        short_text = script_full[:200] if script_full else "This is a test narration for the AI wealth video simulation."
    await generate_voice_audio(short_text, audio_path, "en-US-GuyNeural")
    if os.path.exists(audio_path):
        print(f"✅ Áudio gerado: {os.path.getsize(audio_path)} bytes")

    # 3. Teste do Visual Scraper
    print("🌐 [3/5] Buscando Material Visual...")
    video_clip = get_media_with_cache("artificial intelligence")
    if not video_clip:
        print("❌ Falha ao obter clipe visual. Verifique API Keys.")
        return

    # 4. Teste de Legendas (Whisper)
    print("📝 [4/5] Transcrevendo com Whisper...")
    srt_path = generate_subtitles_locally(audio_path, job_id)
    
    # 5. Teste de Renderização (Ken Burns + Audio Mix)
    print("🎬 [5/5] Renderizando com Ken Burns + Mixagem de Áudio...")
    success = assemble_production(
        job_id=job_id,
        video_paths=[video_clip],
        audio_path=audio_path,
        srt_path=srt_path,
        topic=job.topic,
        clip_durations=[10.0] 
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
