import os
import sys
import asyncio
from typing import Dict, Any

# Adiciona o diretório raiz ao path para poder importar o desktop_node
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
from desktop_node import render_video as render_video_actual

async def render_video(job_data: Dict[str, Any]) -> str:
    """
    Motor de Renderização Local.
    Consome os dados do script e gera o arquivo MP4 real.
    """
    job_id = job_data["job_id"]
    niche = job_data["niche"]
    
    print(f"🎬 [Renderer] Iniciando renderização real do Job {job_id}")
    
    def run_rendering():
        success = render_video_actual(job_data)
        if not success:
            raise RuntimeError(f"Falha ao renderizar o vídeo para o Job {job_id}")
        
        output_dir = os.path.join(os.path.abspath("."), "videos_finais")
        final_video_path = os.path.join(output_dir, f"final_{job_id}.mp4")
        
        if not os.path.exists(final_video_path):
            short_path = os.path.join(output_dir, f"shorts_1_{job_id}.mp4")
            if os.path.exists(short_path):
                return short_path
            raise FileNotFoundError(f"Arquivo final renderizado não encontrado em: {final_video_path}")
            
        return final_video_path

    video_output = await asyncio.to_thread(run_rendering)
    print(f"✅ [Renderer] Vídeo pronto: {video_output}")
    return video_output
