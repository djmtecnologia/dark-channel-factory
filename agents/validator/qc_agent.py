"""
Validator Agent (Quality Control) — Dark Channel Factory
Responsável por auditar os vídeos finais, metadados, thumbnails e hook.
Exclui os arquivos e marca para refação caso não atinjam o padrão elite.
"""
import os
import json
import subprocess
from sqlmodel import Session
from cloud_hub.models import VideoJob, Channel
from shared.logger import get_logger

logger = get_logger(__name__)

class ValidatorAgent:
    def __init__(self, session, job, video_path: str, thumbnail_path: str = None, channel_language: str = "en-US"):
        self.session = session
        self.job = job
        self.video_path = video_path
        self.thumbnail_path = thumbnail_path
        self.errors = []
        
        # Pode receber o DB Model VideoJob ou um dict raw (desktop_node)
        self.job_id = job.id if hasattr(job, 'id') else (job.get('id') or job.get('job_id') or 'unknown')
        self.topic = job.topic if hasattr(job, 'topic') else job.get('topic')
        self.script_data = job.script_data if hasattr(job, 'script_data') else job.get('script_data', {})
        self.video_type = job.video_type if hasattr(job, 'video_type') else (job.get('video_type') or job.get('type') or 'LONG')
        self.affiliate_url = job.injected_affiliate_url if hasattr(job, 'injected_affiliate_url') else job.get('injected_affiliate_url')
        
        if self.session and hasattr(job, 'channel_id'):
            channel = self.session.get(Channel, job.channel_id)
            self.channel_language = channel.language if channel else channel_language
        else:
            self.channel_language = channel_language

    def validate_metadata_and_language(self):
        """Verifica se os metadados existem e se batem com o idioma do canal."""
        # Suporte robusto para dicionários ou objetos
        topic = self.topic or (self.job.get('topic') if isinstance(self.job, dict) else None)
        script_data = self.script_data or (self.job.get('script_data') if isinstance(self.job, dict) else {})
        
        if not topic or not script_data:
            # Tenta um último recurso: verificar se o script_full existe diretamente no script_data
            if isinstance(script_data, dict) and script_data.get('script_full'):
                pass # OK
            else:
                self.errors.append("Metadados (Title/Script) ausentes.")
                return

        # Garante que estamos lendo a string do roteiro independente da estrutura do dict
        script_text = ""
        if isinstance(script_data, dict):
            script_text = script_data.get("script_full") or script_data.get("script") or ""
        else:
            script_text = str(script_data)

        if len(script_text) < 50: # Relaxado de 100 para 50 para evitar falsos positivos
            self.errors.append(f"Roteiro extremamente curto ({len(script_text)} caracteres).")

        # Verifica coerência de idioma (heurística simples)
        lang_target = self.channel_language.lower() # ex: en-us
        if "en" in lang_target:
            if " o " in script_text[:100] and " de " in script_text[:100]:
                self.errors.append("Idioma detectado parece ser PT, mas o canal é EN.")
        elif "pt" in lang_target:
            if " the " in script_text[:100] and " and " in script_text[:100]:
                self.errors.append("Idioma detectado parece ser EN, mas o canal é PT.")

    def validate_technical_video(self):
        """Usa FFprobe para validar a integridade do arquivo de vídeo."""
        if not os.path.exists(self.video_path):
            self.errors.append(f"Arquivo de vídeo não encontrado: {self.video_path}")
            return
            
        size_mb = os.path.getsize(self.video_path) / (1024 * 1024)
        if size_mb < 2.0:
            self.errors.append(f"Arquivo de vídeo suspeitamente pequeno ({size_mb:.1f}MB).")
            return

        cmd = [
            "ffprobe", "-v", "error", "-show_entries", 
            "format=duration:stream=codec_type,width,height",
            "-of", "json", self.video_path
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
            data = json.loads(res.stdout)
            
            streams = data.get("streams", [])
            has_video = any(s.get("codec_type") == "video" for s in streams)
            has_audio = any(s.get("codec_type") == "audio" for s in streams)
            duration = float(data.get("format", {}).get("duration", 0))
            
            if not has_video or not has_audio:
                self.errors.append("Vídeo sem stream de áudio ou vídeo detectado.")
            
            expected_min = 20 if self.video_type == "SHORT" else 720
            if duration < expected_min:
                self.errors.append(f"Duração ({duration:.1f}s) abaixo do mínimo para {self.video_type} (Mínimo: 720s).")
                
            # Verifica resolução
            video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
            if video_stream:
                w, h = int(video_stream.get("width", 0)), int(video_stream.get("height", 0))
                # Detecção flexível: LONG deve ser horizontal, SHORT deve ser vertical
                if self.video_type == "LONG":
                    if w < h: # Invertido!
                         self.errors.append(f"Resolução errada para LONG: {w}x{h} (Deveria ser Horizontal).")
                    elif w < 1280 or h < 720:
                         self.errors.append(f"Resolução muito baixa para LONG: {w}x{h}.")
                elif self.video_type == "SHORT":
                    if w > h: # Invertido!
                         self.errors.append(f"Resolução errada para SHORT: {w}x{h} (Deveria ser Vertical).")
                    elif w < 720 or h < 1280:
                         self.errors.append(f"Resolução muito baixa para SHORT: {w}x{h}.")

        except Exception as e:
            self.errors.append(f"Falha ao executar FFprobe: {e}")

    def validate_visual_density(self):
        """
        Verifica se o bitrate médio é condizente com um vídeo 'Elite'.
        Vídeos Dark reais têm movimento e texturas; arquivos quase vazios (estáticos) 
        têm bitrate baixíssimo e devem ser reprovados.
        """
        if not os.path.exists(self.video_path): return
        
        size_bytes = os.path.getsize(self.video_path)
        
        # Pega a duração via ffprobe
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", self.video_path]
        try:
            duration = float(subprocess.run(cmd, capture_output=True, text=True).stdout.strip())
            if duration > 0:
                bitrate_kbps = (size_bytes * 8) / (duration * 1024)
                
                # Para vídeos longos com loops perfeitos (B-rolls repetidos), o encoder h264 comprime brutalmente.
                # 150 kbps é o piso real para detectar tela preta/estoque; backgrounds animados ficam acima disso.
                limit = 150 if self.video_type == "LONG" else 350
                if bitrate_kbps < limit:
                    self.errors.append(f"Densidade visual insuficiente ({bitrate_kbps:.1f} kbps). O vídeo parece ser estático ou tela preta.")
        except: pass

    def validate_hook_visuals(self):
        """Avalia se o vídeo é apenas uma tela preta em múltiplos pontos (Hook e Meio)."""
        if not os.path.exists(self.video_path): return
        
        temp_dir = os.path.dirname(self.video_path)
        # Amostragem em 5 pontos: 1s, 5s, 10s, 30s e Meio do vídeo
        sample_points = [1, 5, 10, 30]
        
        # Tenta pegar o ponto médio
        try:
            cmd_dur = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", self.video_path]
            duration = float(subprocess.run(cmd_dur, capture_output=True, text=True).stdout.strip())
            if duration > 60: sample_points.append(int(duration/2))
        except: pass

        hook_frames = []
        for s in sample_points:
            out_path = os.path.join(temp_dir, f"qc_frame_{self.job_id}_{s}.jpg")
            # Extrai o frame
            cmd = f'ffmpeg -ss {s} -i "{self.video_path}" -vframes 1 -q:v 2 "{out_path}" -y'
            subprocess.run(cmd, shell=True, capture_output=True)
            if os.path.exists(out_path):
                hook_frames.append(out_path)
        
        # Analisa os frames
        black_frames = 0
        for frame in hook_frames:
            size_kb = os.path.getsize(frame) / 1024
            # Limiar calibrado: frames dark-cinéticos reais ficam acima de 12KB;
            # apenas telas 100% pretas ou estocadas ficam abaixo disso.
            if size_kb < 12: 
                black_frames += 1
                
        # Limpeza
        for f in hook_frames:
            try: os.remove(f)
            except: pass
            
        # Se mais da metade dos frames forem 'leves', reprova
        if len(hook_frames) > 0 and (black_frames / len(hook_frames)) > 0.4:
            self.errors.append(f"Reprovado: {black_frames}/{len(hook_frames)} frames analisados são tela preta ou estáticos.")

    def validate_thumbnail(self):
        """Verifica a qualidade técnica da miniatura."""
        if not self.thumbnail_path:
            # Em shorts não exige thumb externa obrigatoriamente
            if self.video_type == "LONG":
                self.errors.append("Thumbnail não informada para vídeo longo.")
            return
            
        if os.path.exists(self.thumbnail_path):
            size_kb = os.path.getsize(self.thumbnail_path) / 1024
            if size_kb < 40:
                self.errors.append(f"Thumbnail com baixa qualidade/resolução (Tamanho: {size_kb:.1f}KB).")
        else:
            self.errors.append(f"Arquivo de Thumbnail não encontrado: {self.thumbnail_path}")

    def validate_affiliate_link(self):
        """Verifica se o link de afiliado injetado está funcionando (não retorna 404 ou 500)."""
        link = self.affiliate_url
        if not link:
            return  # Sem link, não há o que validar

        import requests
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
            # Usamos GET com timeout curto, aceitando redirecionamentos (comum em afiliados)
            res = requests.get(link, headers=headers, timeout=10, allow_redirects=True)
            
            # Bloqueios Cloudflare (403) são normais para bots, então punimos apenas 404 ou erros 5xx de servidor
            if res.status_code == 404 or res.status_code >= 500:
                self.errors.append(f"Link de Afiliado quebrado ou offline (Status {res.status_code}): {link}")
        except requests.exceptions.RequestException as e:
            self.errors.append(f"Falha de conexão com Link de Afiliado (Timeout/DNS): {str(e)[:50]}")

    def execute_quality_control(self) -> bool:
        """Executa toda a bateria de testes e toma a decisão."""
        logger.info(f"🔍 [Validator] Iniciando Controle de Qualidade para Job: {self.job_id}")
        
        self.validate_metadata_and_language()
        self.validate_technical_video()
        self.validate_visual_density()
        self.validate_hook_visuals()
        self.validate_thumbnail()
        self.validate_affiliate_link()
        
        if self.errors:
            logger.error(f"❌ [Validator] Job reprovado. Falhas encontradas:")
            for e in self.errors:
                logger.error(f"   - {e}")
            self._handle_rejection()
            return False
            
        logger.info("✅ [Validator] APROVADO. Padrão Elite verificado (Hook, Metadados, Resolução).")
        return True
        
    def _handle_rejection(self):
        """Move arquivos reprovados para pasta de inspeção em vez de deletar (ajuda no debug)."""
        rejection_dir = os.path.join(os.path.dirname(self.video_path), "rejeitados_qc")
        os.makedirs(rejection_dir, exist_ok=True)
        
        logger.warning(f"⚠️ [Validator] Movendo arquivos reprovados para: {rejection_dir}")
        
        files_to_move = [self.video_path, self.thumbnail_path]
        for f in files_to_move:
            if f and os.path.exists(f):
                try:
                    import shutil
                    dest = os.path.join(rejection_dir, os.path.basename(f))
                    shutil.move(f, dest)
                    logger.info(f"   Movido: {os.path.basename(f)}")
                except Exception as e:
                    logger.error(f"   Erro ao mover {f}: {e}")
                    
        # Salva o motivo detalhado do erro no Job
        error_summary = " | ".join(self.errors)
        if self.session and hasattr(self.job, 'status'):
            self.job.status = "FAILED_QC"
            self.job.error_message = f"QC REJECTED: {error_summary}"
            self.session.add(self.job)
            self.session.commit()
            logger.info(f"🔄 [Validator] Job atualizado com feedback de erro detalhado.")
        else:
            # Para o desktop_node que não tem sessão ativa persistente
            print(f"🚨 [FEEDBACK QC] {error_summary}")

def run_qc_pipeline(session: Session, job: VideoJob, video_path: str, thumbnail_path: str = None) -> bool:
    agent = ValidatorAgent(session, job, video_path, thumbnail_path)
    return agent.execute_quality_control()
