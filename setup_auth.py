"""
setup_auth.py — Dark Channel Factory
Script de setup único para autenticação OAuth do YouTube.

Execute UMA VEZ antes de começar:
    python setup_auth.py

Isso abrirá o navegador para você autorizar o app com derikmoretti@gmail.com
e salvará o token em credentials/youtube_token.json
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

load_dotenv()
console = Console()


def setup_youtube_auth() -> None:
    """Configura autenticação OAuth 2.0 para YouTube Data API."""
    console.print(Panel.fit(
        "[bold cyan]YouTube OAuth Setup[/bold cyan]\n"
        "[dim]Dark Channel Factory v1.0[/dim]",
        border_style="cyan"
    ))

    # Busca o primeiro client_secret na raiz se não estiver no env
    secrets_in_root = [f for f in os.listdir(".") if f.startswith("client_secret") and f.endswith(".json")]
    default_secret = secrets_in_root[0] if secrets_in_root else "client_secrets.json"
    
    client_secrets = Path(os.getenv("YOUTUBE_CLIENT_SECRETS_FILE", default_secret))
    token_file = Path(os.getenv("YOUTUBE_TOKEN_FILE", "youtube_token.json"))

    new_email = "iabusinessus@gmail.com"
    console.print(f"\n[bold]Account:[/bold] {new_email}")
    console.print(f"[bold]Client secrets:[/bold] {client_secrets}")
    console.print(f"[bold]Token output:[/bold] {token_file}\n")

    # ── Verificar client_secrets.json ─────────────────────────
    if not client_secrets.exists():
        console.print("[bold red]❌ client_secrets.json not found![/bold red]")
        console.print("\n[bold]Setup Instructions:[/bold]")
        console.print("1. Go to: [link]https://console.cloud.google.com[/link]")
        console.print("2. Create a project or select existing")
        console.print("3. Enable 'YouTube Data API v3' in API Library")
        console.print("4. Go to Credentials → Create OAuth 2.0 Client ID")
        console.print("5. Application type: [bold]Desktop app[/bold]")
        console.print("6. Download JSON → rename to [bold]client_secrets.json[/bold]")
        console.print(f"7. Place in: [bold]{client_secrets}[/bold]")
        console.print("\n[dim]Also enable 'YouTube Analytics API' for CTR/retention metrics.[/dim]")
        sys.exit(1)

    # ── Se token já existe, verificar validade ─────────────────
    if token_file.exists():
        console.print("[yellow]⚠️  Token already exists. Testing validity...[/yellow]")
        if _test_existing_token(token_file):
            console.print("[green]✅ Token is valid! No re-authentication needed.[/green]")
            return
        else:
            console.print("[yellow]Token expired. Re-authenticating...[/yellow]")
            token_file.unlink()

    # ── Iniciar fluxo OAuth ────────────────────────────────────
    console.print("[bold]🌐 Opening browser for OAuth consent...[/bold]")
    console.print("[dim]Sign in with iabusinessus@gmail.com when prompted[/dim]\n")

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore
        from google.oauth2.credentials import Credentials

        SCOPES = [
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube",
            "https://www.googleapis.com/auth/youtube.force-ssl",
            "https://www.googleapis.com/auth/yt-analytics.readonly",
        ]

        flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent")

        # Salvar token
        token_file.parent.mkdir(parents=True, exist_ok=True)
        with open(token_file, "w") as f:
            f.write(creds.to_json())

        console.print(f"\n[bold green]✅ Authentication successful![/bold green]")
        console.print(f"Token saved to: [bold]{token_file}[/bold]")
        console.print("\n[bold]Next steps:[/bold]")
        console.print("1. Copy [bold].env.example[/bold] to [bold].env[/bold] and fill in your API keys")
        console.print("2. Add your API keys: GEMINI_API_KEY, PEXELS_API_KEY")
        console.print("3. Start Redis: [code]docker-compose up -d redis[/code]")
        console.print("4. Test pipeline: [code]python -m orchestrator.main --now --dry-run[/code]")
        console.print("5. Go live: [code]python -m orchestrator.main[/code]")

    except ImportError:
        console.print("[red]Install dependencies first: pip install -r requirements.txt[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[bold red]Auth failed: {e}[/bold red]")
        sys.exit(1)


def _test_existing_token(token_file: Path) -> bool:
    """Testa se o token existente é válido."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        creds = Credentials.from_authorized_user_file(str(token_file))

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(token_file, "w") as f:
                f.write(creds.to_json())

        # Testa com chamada simples
        youtube = build("youtube", "v3", credentials=creds)
        youtube.channels().list(part="snippet", mine=True).execute()
        return True
    except Exception:
        return False


if __name__ == "__main__":
    setup_youtube_auth()
