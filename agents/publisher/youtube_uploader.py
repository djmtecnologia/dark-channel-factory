import os
import time
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
from agents.publisher.endscreen_manager import setup_endscreens

# Cache global para economizar cota de API (search costs 100 units)
WATCH_NEXT_CACHE = {}

# Escopo necessário para fazer upload e gerenciar vídeos (incluindo comentários)
SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/youtube.force-ssl'
]

def get_authenticated_service(channel_identifier: str, secret_index: int = 1):
    """
    Autentica usando um índice de segredo específico (ex: client_secret_1.json, client_secret_2.json...)
    """
    creds = None
    # Configuração de Arquivos com Índice
    token_file = f"token_{channel_identifier}_secret_{secret_index}.json"
    client_secrets_file = f"client_secret_{secret_index}.json"
    
    # Fallback para o arquivo padrão se for o índice 1 e o arquivo indexado não existir
    if secret_index == 1 and not os.path.exists(client_secrets_file):
        client_secrets_file = "client_secret.json"

    # Mapa de Contas Oficiais por Idioma
    CHANNEL_ACCOUNTS = {
        "en-US": "iabusinessus@gmail.com", # <--- CORRIGIDO
        "fr-FR": "docelarmercadotudo@gmail.com",
        "es-ES": "lightpublicidadedkm@gmail.com",
        "de-DE": "mentedivinahumana@gmail.com"
    }
    
    target_email = CHANNEL_ACCOUNTS.get(channel_identifier, "Conta Associada")

    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except:
                creds = None
        
        if not creds:
            if not os.path.exists(client_secrets_file):
                return None # Indica que esta chave/segredo não existe para rotação
            
            print(f"\n🔑 [ROTAÇÃO] Autenticando com Chave #{secret_index} para o canal {channel_identifier} ({target_email})...")
            flow = InstalledAppFlow.from_client_secrets_file(client_secrets_file, SCOPES)
            # Injeta o login_hint para abrir o perfil correto automaticamente
            creds = flow.run_local_server(port=0, timeout_seconds=3600, login_hint=target_email)
            
        with open(token_file, 'w') as token:
            token.write(creds.to_json())
            
    return build('youtube', 'v3', credentials=creds)

def publish_video_to_youtube(video_path: str, thumbnail_path: str, title: str, description: str, tags: list, channel_identifier: str, privacy_status: str = "public", pinned_comment: str = None, is_short: bool = False, scheduled_at = None):
    """
    Realiza o upload oficial e fixa comentário se fornecido. Suporta agendamento via scheduled_at.
    """
    print("\n=============================================")
    print(f"🔴 [YOUTUBE API] Iniciando Motor de Upload com Rotação")
    print(f"👤 Canal Destino: {channel_identifier}")
    print("=============================================")
    
    used_projects = set()
    for index in range(1, 11):
        try:
            # 1. Detecta o ID do projeto para evitar duplicidade de cota
            client_secrets_file = f"client_secret_{index}.json"
            if index == 1 and not os.path.exists(client_secrets_file): client_secrets_file = "client_secret.json"
            
            if os.path.exists(client_secrets_file):
                import json
                with open(client_secrets_file, "r") as f:
                    try:
                        data = json.load(f)
                        pid = data.get("installed", {}).get("project_id") or data.get("web", {}).get("project_id")
                        if pid in used_projects:
                            print(f"⏩ [Chave #{index}] Pulando projeto já usado: {pid}")
                            continue
                        if pid: used_projects.add(pid)
                    except: pass

            youtube = get_authenticated_service(channel_identifier, secret_index=index)
            
            if youtube is None:
                if index > 1: break
                raise FileNotFoundError("Nenhum client_secret.json encontrado.")

            file_size_mb = os.path.getsize(video_path) / (1024*1024) if os.path.exists(video_path) else 0
            print(f"📦 [Chave #{index}] Preparando upload...")
            
            # [ESTRATÉGIA VIRAL] Busca último vídeo para Cross-Promotion (COM CACHE)
            global WATCH_NEXT_CACHE
            watch_next_link = WATCH_NEXT_CACHE.get(channel_identifier, "")
            
            if not watch_next_link:
                try:
                    print(f"🔍 [Quota] Buscando último vídeo para cross-promotion...")
                    search_res = youtube.search().list(part="snippet", forMine=True, type="video", order="date", maxResults=1).execute()
                    if search_res.get("items"):
                        last_vid_id = search_res["items"][0]["id"]["videoId"]
                        watch_next_link = f"https://www.youtube.com/watch?v={last_vid_id}"
                        WATCH_NEXT_CACHE[channel_identifier] = watch_next_link
                except Exception as e:
                    print(f"⚠️ Erro ao buscar último vídeo: {e}")
            
            # Atualiza o cache com o link do vídeo ATUAL para o PRÓXIMO upload
            # (Isso economiza uma busca por vídeo)
            # Mas só fazemos isso APÓS o upload ter sucesso.

            final_description = description
            if watch_next_link:
                final_description = f"🔥 WATCH THIS NEXT: {watch_next_link}\n\n" + description

            body = {
                'snippet': {
                    'title': title[:100],
                    'description': final_description[:5000],
                    'tags': tags[:15],
                    'categoryId': '28',
                    'defaultAudioLanguage': 'en',
                    'defaultLanguage': 'en'
                },
                'status': {
                    'privacyStatus': privacy_status,
                    'selfDeclaredMadeForKids': False
                }
            }

            if scheduled_at:
                body['status']['privacyStatus'] = 'private'
                body['status']['publishAt'] = scheduled_at.strftime('%Y-%m-%dT%H:%M:%SZ')
                print(f"⏰ [Agendamento] Vídeo agendado para: {body['status']['publishAt']}")
            
            print(f"⬆️ [Upload] Enviando vídeo...")
            media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
            request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
            
            response = None
            while response is None:
                status, response = request.next_chunk()
            
            video_id = response.get('id')
            print(f"✅ Upload Finalizado (ID: {video_id})")
            
            # Atualiza o cache para o próximo vídeo usar este como recomendação
            WATCH_NEXT_CACHE[channel_identifier] = f"https://www.youtube.com/watch?v={video_id}"

            # Thumbnail
            if thumbnail_path and os.path.exists(thumbnail_path):
                print(f"🖼️ Enviando Thumbnail...")
                youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(thumbnail_path)).execute()
                
            # Comentário Fixado (Com Link de Monetização + Link de Cross-Promotion)
            final_pinned = pinned_comment or ""
            if watch_next_link:
                final_pinned += f"\n\n🚀 Next Video to Watch: {watch_next_link}"

            if final_pinned:
                print(f"💬 Postando comentário fixado estratégico...")
                try:
                    comment_res = youtube.commentThreads().insert(
                        part="snippet",
                        body={
                            "snippet": {
                                "videoId": video_id,
                                "topLevelComment": {
                                    "snippet": {"textOriginal": final_pinned}
                                }
                            }
                        }
                    ).execute()
                except HttpError as e:
                    if "forbidden" in str(e).lower() or "disabled" in str(e).lower():
                        print(f"⚠️ Aviso: Não foi possível postar o comentário. O vídeo pode estar agendado/privado ou com comentários desativados temporariamente.")
                    else:
                        print(f"⚠️ Erro ao postar comentário: {e}")
            
            # [AUTOMAÇÃO VISUAL] Configura Telas Finais via Selenium
            # Apenas para vídeos longos (Shorts não têm tela final configurável via Studio da mesma forma)
            if not is_short:
                try:
                    setup_endscreens(video_id)
                except Exception as e:
                    print(f"⚠️ Erro na automação de tela final: {e}")

            video_url = f"https://www.youtube.com/watch?v={video_id}"
            print("\n🎉 [SUCESSO TOTAL] VÍDEO ONLINE!")
            return video_url

        except HttpError as e:
            error_data = str(e)
            if "uploadLimitExceeded" in error_data or "quotaExceeded" in error_data:
                print(f"⚠️ [COTA CHEIA] Chave #{index} esgotada. Tentando próxima chave...")
                continue # Pula para o próximo índice no loop
            else:
                print(f"❌ [ERRO API] {e}")
                raise e
        except Exception as e:
            print(f"❌ [ERRO GERAL] {e}")
            raise e

    raise Exception("Todas as chaves de API disponíveis foram esgotadas para hoje.")
