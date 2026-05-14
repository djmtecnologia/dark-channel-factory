"""
YouTube Uploader — Manager & Analyst Agent
Upload de vídeos via YouTube Data API v3 com protocolo resumable.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

from shared.logger import get_logger

load_dotenv()
logger = get_logger(__name__)

YOUTUBE_TOKEN_FILE = os.getenv("YOUTUBE_TOKEN_FILE", "credentials/youtube_token.json")
YOUTUBE_CLIENT_SECRETS = os.getenv("YOUTUBE_CLIENT_SECRETS_FILE", "credentials/client_secrets.json")
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

# Categorias YouTube
CATEGORY_SCIENCE_TECH = "28"
CATEGORY_EDUCATION = "27"
CATEGORY_HOWTO = "26"

SKIP_UPLOAD = os.getenv("SKIP_UPLOAD", "false").lower() == "true"


def _build_youtube_service():
    """Constrói serviço autenticado do YouTube."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    token_path = Path(YOUTUBE_TOKEN_FILE)
    secrets_path = Path(YOUTUBE_CLIENT_SECRETS)

    # Carrega token existente
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), YOUTUBE_SCOPES)

    # Renova token expirado
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save_credentials(creds)
    elif not creds or not creds.valid:
        if not secrets_path.exists():
            raise FileNotFoundError(
                f"client_secrets.json not found at {secrets_path}.\n"
                "Run: python setup_auth.py"
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), YOUTUBE_SCOPES)
        creds = flow.run_local_server(port=0)
        _save_credentials(creds)

    return build("youtube", "v3", credentials=creds)


def _save_credentials(creds) -> None:
    """Salva token OAuth."""
    token_path = Path(YOUTUBE_TOKEN_FILE)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    with open(token_path, "w") as f:
        f.write(creds.to_json())


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=5, max=60))
def upload_video(
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
    category_id: str = CATEGORY_SCIENCE_TECH,
    thumbnail_path: str | None = None,
    scheduled_at: datetime | None = None,
) -> str:
    """
    Faz upload de vídeo para YouTube via protocolo resumable.
    Retorna o YouTube video ID.

    Args:
        video_path: Caminho do arquivo .mp4
        title: Título do vídeo
        description: Descrição completa
        tags: Lista de tags (max 500 chars total)
        category_id: ID da categoria YouTube
        thumbnail_path: Caminho da thumbnail .png/.jpg
        scheduled_at: Data de publicação (None = público imediatamente)
    """
    if SKIP_UPLOAD:
        mock_id = f"MOCK_{int(time.time())}"
        logger.info(f"[SKIP_UPLOAD] Would upload '{title}' → mock ID: {mock_id}")
        return mock_id

    from googleapiclient.http import MediaFileUpload

    if not Path(video_path).exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    youtube = _build_youtube_service()

    # Determina privacy status
    if scheduled_at:
        privacy_status = "private"  # Será mudado para 'public' no horário agendado
        publish_at = scheduled_at.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    else:
        privacy_status = "public"
        publish_at = None

    # Body da requisição
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": _trim_tags(tags),
            "categoryId": category_id,
            "defaultLanguage": "en",
            "defaultAudioLanguage": "en",
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
            "madeForKids": False,
        },
    }

    # Adiciona AI content disclosure (obrigatório desde 2024)
    body["status"]["containsSyntheticMedia"] = True

    if publish_at:
        body["status"]["publishAt"] = publish_at

    # Upload com protocolo resumable
    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=50 * 1024 * 1024,  # 50MB chunks
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    video_id = None
    logger.info(f"📤 [Uploader] Uploading: '{title[:50]}...'")

    # Upload progressivo com retry
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            progress = int(status.progress() * 100)
            logger.info(f"📤 [Uploader] Upload progress: {progress}%")

    video_id = response.get("id")
    logger.info(f"✅ [Uploader] Upload complete! Video ID: {video_id}")

    # Seta thumbnail
    if thumbnail_path and Path(thumbnail_path).exists() and video_id:
        try:
            _set_thumbnail(youtube, video_id, thumbnail_path)
        except Exception as e:
            logger.warning(f"Thumbnail upload failed (non-critical): {e}")

    return video_id


def _set_thumbnail(youtube, video_id: str, thumbnail_path: str) -> None:
    """Define thumbnail do vídeo."""
    from googleapiclient.http import MediaFileUpload

    mime_type = "image/png" if thumbnail_path.endswith(".png") else "image/jpeg"
    media = MediaFileUpload(thumbnail_path, mimetype=mime_type)

    youtube.thumbnails().set(
        videoId=video_id,
        media_body=media,
    ).execute()
    logger.info(f"✅ [Uploader] Thumbnail set for video {video_id}")


def update_thumbnail(video_id: str, thumbnail_path: str) -> bool:
    """Troca thumbnail de um vídeo já publicado (para A/B test)."""
    try:
        youtube = _build_youtube_service()
        _set_thumbnail(youtube, video_id, thumbnail_path)
        logger.info(f"🔄 [Uploader] Thumbnail updated for {video_id}")
        return True
    except Exception as e:
        logger.error(f"Thumbnail update failed for {video_id}: {e}")
        return False


def post_pinned_comment(video_id: str, comment_text: str) -> str | None:
    """Posta e fixa o primeiro comentário com links de afiliado."""
    if SKIP_UPLOAD:
        logger.info(f"[SKIP_UPLOAD] Would post pinned comment on {video_id}")
        return "MOCK_COMMENT_ID"

    try:
        youtube = _build_youtube_service()

        # Posta comentário
        comment_response = youtube.commentThreads().insert(
            part="snippet",
            body={
                "snippet": {
                    "videoId": video_id,
                    "topLevelComment": {
                        "snippet": {
                            "textOriginal": comment_text[:10000],
                        }
                    }
                }
            }
        ).execute()

        comment_id = comment_response.get("id")
        logger.info(f"✅ [Uploader] Pinned comment posted: {comment_id}")
        return comment_id

    except Exception as e:
        logger.error(f"Comment posting failed: {e}")
        return None


def _trim_tags(tags: list[str]) -> list[str]:
    """Garante que tags não excedam 500 chars total."""
    result = []
    total_chars = 0
    for tag in tags:
        tag = tag[:50]  # Max 50 chars por tag
        if total_chars + len(tag) > 500:
            break
        result.append(tag)
        total_chars += len(tag)
    return result


def get_scheduled_publish_time(
    publish_days: list[str],
    publish_time_utc: str = "14:00",
) -> datetime:
    """
    Calcula próxima data de publicação agendada.
    Returns datetime UTC do próximo horário de publicação configurado.
    """
    from datetime import datetime, timedelta, timezone

    day_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

    now = datetime.now(timezone.utc)
    hour, minute = map(int, publish_time_utc.split(":"))

    target_days = [day_names.index(d.lower()) for d in publish_days if d.lower() in day_names]

    if not target_days:
        # Default: 1 hora a partir de agora
        return now + timedelta(hours=1)

    for days_ahead in range(8):
        candidate = now + timedelta(days=days_ahead)
        candidate_weekday = candidate.weekday()

        if candidate_weekday in target_days:
            scheduled = candidate.replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )
            if scheduled > now + timedelta(hours=1):
                return scheduled

    # Fallback: 3 dias à frente
    return now + timedelta(days=3, hours=hour, minutes=minute)
