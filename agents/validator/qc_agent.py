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
from shared.production_profiles import resolve_profile, validate_duration, ProductionProfileConfig

logger = get_logger(__name__)

class ValidatorAgent:
    def __init__(self, session, job, video_path: str, thumbnail_path: str = None, srt_path: str = None, channel_language: str = "en-US", production_profile: str = None):
        self.session = session
        self.job = job
        self.video_path = video_path
        self.thumbnail_path = thumbnail_path
        self.srt_path = srt_path
        self.errors = []
        
        # Pode receber o DB Model VideoJob ou um dict raw (desktop_node)
        self.job_id = job.id if hasattr(job, 'id') else (job.get('id') or job.get('job_id') or 'unknown')
        self.topic = job.topic if hasattr(job, 'topic') else job.get('topic')
        self.script_data = job.script_data if hasattr(job, 'script_data') else job.get('script_data', {})
        self.video_type = job.video_type if hasattr(job, 'video_type') else (job.get('video_type') or job.get('type') or 'LONG')
        self.affiliate_url = job.injected_affiliate_url if hasattr(job, 'injected_affiliate_url') else job.get('injected_affiliate_url')
        
        # Production profile resolution
        raw_profile = (
            production_profile
            or getattr(job, 'production_profile', None)
            or (job.get('production_profile') if isinstance(job, dict) else None)
            or (job.get('profile') if isinstance(job, dict) else None)
        )
        self.production_profile = str(raw_profile).strip() if raw_profile else None
        self.resolved_profile: ProductionProfileConfig | None = resolve_profile(
            job=job,
            profile_name=self.production_profile
        )
        
        if self.session and hasattr(job, 'channel_id'):
            channel = self.session.get(Channel, job.channel_id)
            self.channel_language = channel.language if channel else channel_language
        else:
            self.channel_language = channel_language

        # Resolução absoluta de FFmpeg e FFprobe locais
        root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.ffmpeg_exe = os.path.join(root_dir, "bin", "ffmpeg.exe")
        if not os.path.exists(self.ffmpeg_exe):
            self.ffmpeg_exe = "ffmpeg"
            
        self.ffprobe_exe = os.path.join(root_dir, "bin", "ffprobe.exe")
        if not os.path.exists(self.ffprobe_exe):
            self.ffprobe_exe = "ffprobe"

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
            self.ffprobe_exe, "-v", "error", "-show_entries", 
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
            
            # Validação centralizada de duração via Production Profile
            duration_ok, duration_err = validate_duration(
                duration_s=duration,
                profile=self.resolved_profile,
                video_type=self.video_type
            )
            if not duration_ok and duration_err:
                self.errors.append(duration_err)
                
            # Verifica resolução
            video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
            if video_stream:
                w, h = int(video_stream.get("width", 0)), int(video_stream.get("height", 0))
                is_vert = self.resolved_profile.is_vertical if self.resolved_profile else (self.video_type == "SHORT")
                profile_lbl = self.resolved_profile.name if self.resolved_profile else self.video_type

                if is_vert:
                    if w > h:
                        self.errors.append(f"Resolução errada para {profile_lbl}: {w}x{h} (Deveria ser Vertical).")
                    elif w < 720 or h < 1280:
                        self.errors.append(f"Resolução muito baixa para {profile_lbl}: {w}x{h}.")
                else:
                    if w < h:
                        self.errors.append(f"Resolução errada para {profile_lbl}: {w}x{h} (Deveria ser Horizontal).")
                    elif w < 1280 or h < 720:
                        self.errors.append(f"Resolução muito baixa para {profile_lbl}: {w}x{h}.")

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
        cmd = [self.ffprobe_exe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", self.video_path]
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
        """Avalia se o vídeo é apenas uma tela preta ou estática de baixíssimo contraste em múltiplos pontos."""
        if not os.path.exists(self.video_path): return
        
        temp_dir = os.path.dirname(self.video_path)
        # Amostragem em 5 pontos seguros: evita o primeiro segundo (s=1) que pode ser um fade-in preto natural
        sample_points = [3, 8, 15, 30]
        
        # Tenta pegar o ponto médio
        try:
            cmd_dur = [self.ffprobe_exe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", self.video_path]
            duration = float(subprocess.run(cmd_dur, capture_output=True, text=True).stdout.strip())
            if duration > 60: sample_points.append(int(duration/2))
        except: pass

        hook_frames = []
        for s in sample_points:
            out_path = os.path.join(temp_dir, f"qc_frame_{self.job_id}_{s}.jpg")
            # Extrai o frame (chamada robusta com lista, evitando shell=True no Windows)
            cmd = [
                self.ffmpeg_exe, "-ss", str(s), "-i", self.video_path,
                "-vframes", "1", "-q:v", "2", out_path, "-y"
            ]
            subprocess.run(cmd, capture_output=True)
            if os.path.exists(out_path):
                hook_frames.append(out_path)
        
        # Analisa os frames usando Pillow (Luminosidade Média e Desvio Padrão)
        black_frames = 0
        from PIL import Image, ImageStat
        
        for frame in hook_frames:
            try:
                with Image.open(frame) as img:
                    gray_img = img.convert("L")
                    
                    # [BLINDAGEM] Recorta os primeiros 60% de altura do frame (metade superior) para
                    # ignorar pixels de legendas brilhantes (que ficam na parte inferior/centro-inferior).
                    # Isso garante que a avaliação de tela preta incida apenas sobre a imagem/vídeo de fundo.
                    w_img, h_img = gray_img.size
                    cropped_img = gray_img.crop((0, 0, w_img, int(h_img * 0.6)))
                    
                    stat = ImageStat.Stat(cropped_img)
                    mean_brightness = stat.mean[0]
                    stddev = stat.stddev[0]
                    
                    # Cenas legítimas escuras e nítidas possuem stddev alto (contraste).
                    # Telas pretas e estáticas vazias possuem tanto brilho muito baixo quanto desvio padrão baixíssimo (quase plano/0).
                    if mean_brightness < 8.0 and stddev < 3.5:
                        black_frames += 1
                        logger.warning(f"⚠️ [QC] Frame {os.path.basename(frame)} detectado como tela preta/escura (Brilho: {mean_brightness:.2f}, DP: {stddev:.2f})")
            except Exception as e:
                logger.error(f"⚠️ Erro ao ler frame no QC Pillow: {e}")
                # Fallback seguro para o tamanho do arquivo
                size_kb = os.path.getsize(frame) / 1024
                if size_kb < 7.0:
                    black_frames += 1
                
        # Limpeza
        for f in hook_frames:
            try: os.remove(f)
            except: pass
            
        # [CORREÇÃO CRÍTICA] Se falhar ao extrair frames (ex: ffmpeg indisponível), reprova com falha técnica!
        # Isso impede aprovação cega e garante a integridade absoluta.
        if len(hook_frames) == 0:
            self.errors.append("Falha técnica crítica: Não foi possível extrair frames do vídeo para verificação visual do QC.")
        elif (black_frames / len(hook_frames)) > 0.5: # Tolerância estendida para 50% para evitar falsos positivos
            self.errors.append(f"Reprovado: {black_frames}/{len(hook_frames)} frames analisados são tela preta ou estáticos.")

    def validate_subtitles(self):
        """Verifica se o arquivo de legendas SRT existe, é válido e contém blocos de texto reais."""
        srt_path = self.srt_path
        
        # Se não for passado explicitamente, tenta inferir a partir do caminho do vídeo
        if not srt_path and self.video_path:
            base_dir = os.path.dirname(self.video_path)
            base_name = os.path.basename(self.video_path)
            # Tenta substituir a extensão do vídeo por .srt
            srt_candidate = os.path.join(base_dir, os.path.splitext(base_name)[0] + ".srt")
            if os.path.exists(srt_candidate):
                srt_path = srt_candidate
        
        if not srt_path:
            # Em shorts a legenda pode estar 100% embutida sem arquivo externo para o upload,
            # mas se for vídeo longo, a legenda SRT externa é obrigatória para o SEO
            if self.video_type == "LONG":
                self.errors.append("Arquivo de legenda SRT não encontrado.")
            return

        if not os.path.exists(srt_path):
            if self.video_type == "LONG":
                self.errors.append(f"Arquivo de legenda SRT inexistente no caminho: {srt_path}")
            return
            
        size_bytes = os.path.getsize(srt_path)
        if size_bytes < 50: # Extremamente pequeno/vazio
            self.errors.append(f"Arquivo de legenda SRT suspeitosamente vazio ({size_bytes} bytes).")
            return

        # Validação de conteúdo básico do SRT (blocos do tipo: index, timestamps, text)
        try:
            with open(srt_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            
            # Um arquivo SRT válido deve ter pelo menos alguns números de bloco e marcadores de tempo -->
            if "-->" not in content:
                self.errors.append("Arquivo de legenda SRT corrompido ou em formato inválido (ausência de marcadores de tempo '-->').")
                return
                
            # Verifica se contém blocos vazios ou se a contagem de blocos é muito baixa para o tamanho do vídeo
            blocks = [b.strip() for b in content.split("\n\n") if b.strip()]
            valid_blocks = [b for b in blocks if len(b.split("\n")) >= 3]
            
            if len(valid_blocks) < 3 and self.video_type == "LONG":
                self.errors.append(f"Arquivo de legenda SRT possui blocos de texto insuficientes ({len(valid_blocks)} blocos).")
                
            logger.info(f"📝 [Validator] Legenda SRT validada com sucesso: {len(valid_blocks)} blocos de texto encontrados.")
        except Exception as e:
            self.errors.append(f"Erro ao analisar o arquivo de legenda SRT: {e}")

    def validate_thumbnail(self):
        """Verifica a qualidade técnica da miniatura."""
        if not self.thumbnail_path:
            # Em shorts não exige thumb externa obrigatoriamente
            if self.video_type == "LONG":
                self.errors.append("Thumbnail não informada para vídeo longo.")
            return
            
        if os.path.exists(self.thumbnail_path):
            size_kb = os.path.getsize(self.thumbnail_path) / 1024
            if size_kb < 28.0:
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
        self.validate_subtitles()
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
