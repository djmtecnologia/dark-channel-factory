"""
Multi-Channel Setup — Dark Channel Factory
Usa perfis existentes do Chrome para configurar canais no YouTube Studio.
Funciona com sessões já logadas — sem senha, sem bot detection.

COMO USAR:
    python channel_setup/multi_channel_setup.py --list
    python channel_setup/multi_channel_setup.py --setup-all
    python channel_setup/multi_channel_setup.py --profile "Profile 1" --niche ai_tools --lang en-US
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from channel_setup.chrome_profile_mapper import CHROME_USER_DATA, get_chrome_profiles

# ─── Mapeamento de canais a configurar ───────────────────────────────────────
# Edite aqui para mapear cada email ao niche e idioma desejado.
# O sistema lerá automaticamente a sessão do Chrome correspondente.

CHANNEL_ASSIGNMENTS = {
    "derikmoretti@gmail.com": {
        "niche": "ai_tools",
        "language": "en-US",
        "channel_name": "AI Tools Unlocked",
        "channel_description": "Exploring the most powerful AI tools and automation strategies to change your business.",
        "channel_keywords": ["AI", "Automation", "GPT", "Productivity"],
    },
    "djmtecnologiasystem@gmail.com": {
        "niche": "ai_tools",
        "language": "en-GB",
        "channel_name": "Pro Automation UK",
        "channel_description": "Professional business automation and AI integration for the modern era.",
        "channel_keywords": ["Business AI", "UK Tech", "Automation"],
    },
    "aquieagoranews@gmail.com": {
        "niche": "psychology_curiosity",
        "language": "en-US",
        "channel_name": "Mind Hack Secrets",
        "channel_description": "Unlocking the secrets of human behavior and psychology.",
        "channel_keywords": ["Psychology", "Mind Hacks", "Human Nature"],
    },
    "playmatchesportes@gmail.com": {
        "niche": "personal_finance",
        "language": "en-US",
        "channel_name": "Pulse Finance",
        "channel_description": "High-yield investment strategies and financial freedom hacks.",
        "channel_keywords": ["Finance", "Investing", "Wealth"],
    },
    "docelarmercadotudo@gmail.com": {
        "niche": "health_longevity",
        "language": "en-US",
        "channel_name": "Life Optimizer",
        "channel_description": "Optimizing health and longevity through science and biohacking.",
        "channel_keywords": ["Health", "Longevity", "Biohacking"],
    },
    "mentedivinahumana@gmail.com": {
        "niche": "psychology_curiosity",
        "language": "es-ES",
        "channel_name": "Mente Divina",
        "channel_description": "Descifrando los misterios de la mente humana y la psicología.",
        "channel_keywords": ["Psicología", "Cerebro", "Curiosidades"],
    },
    "lightpublicidadedkm@gmail.com": {
        "niche": "ai_tools",
        "language": "es-ES",
        "channel_name": "Tecno Pulso",
        "channel_description": "Las mejores herramientas de IA para potenciar tu productividad.",
        "channel_keywords": ["IA", "Tecnología", "Productividad"],
    },
    "jaque_viana@hotmail.com": {
        "niche": "health_longevity",
        "language": "de-DE",
        "channel_name": "Vitalität Heute",
        "channel_description": "Ihre tägliche Dosis Gesundheit und Langlebigkeit.",
        "channel_keywords": ["Gesundheit", "Wellness", "Langlebigkeit"],
    },
}

# Configuração de canal por niche (defaults)
NICHE_CONFIGS = {
    "ai_tools": {
        "description_template": "🤖 AI tools, business automation & productivity hacks. 3 videos/week.",
        "keywords": ["AI tools", "artificial intelligence", "automation", "ChatGPT", "productivity"],
        "category_id": "28",
    },
    "personal_finance": {
        "description_template": "💰 Investing, passive income & financial freedom strategies.",
        "keywords": ["investing", "passive income", "financial freedom", "money", "wealth"],
        "category_id": "20",
    },
    "psychology_curiosity": {
        "description_template": "🧠 Mind-blowing psychology facts, human behavior & mental hacks.",
        "keywords": ["psychology", "human behavior", "mental health", "curiosity", "mind"],
        "category_id": "26",
    },
    "health_longevity": {
        "description_template": "💪 Science-backed longevity, health optimization & biohacking.",
        "keywords": ["longevity", "health", "biohacking", "fitness", "wellness"],
        "category_id": "26",
    },
}


async def setup_youtube_channel(
    chrome_profile_path: str,
    channel_name: str,
    channel_description: str,
    channel_keywords: list[str],
    niche: str,
    language: str,
    logo_path: str | None = None,
    banner_path: str | None = None,
) -> bool:
    """
    Configura um canal no YouTube Studio usando um perfil Chrome existente.
    Usa a sessão já logada — NÃO precisa de senha.
    """
    from playwright.async_api import async_playwright

    print(f"\n🎬 Setting up channel: {channel_name}")
    print(f"   Profile: {chrome_profile_path}")
    print(f"   Niche: {niche} | Language: {language}")

    # Cria cópia temporária do perfil para não travar o Chrome principal
    temp_dir = tempfile.mkdtemp(prefix="dcf_chrome_")
    temp_profile = Path(temp_dir) / "profile"

    try:
        print(f"   📁 Copying Chrome profile (may take a moment)...")
        shutil.copytree(chrome_profile_path, str(temp_profile), dirs_exist_ok=True)

        async with async_playwright() as p:
            browser = await p.chromium.launch_persistent_context(
                user_data_dir=str(temp_dir),
                headless=False,
                channel="chrome",   # Usa Chrome instalado (não Chromium)
                args=[
                    "--start-maximized",
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                ],
                viewport=None,
                ignore_https_errors=True,
            )

            page = browser.pages[0] if browser.pages else await browser.new_page()

            # ── Verificar se está logado ──────────────────────────
            print("   🔑 Checking login status...")
            await page.goto("https://studio.youtube.com", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(4)

            if "accounts.google.com" in page.url or "signin" in page.url:
                print(f"   ⚠️  Not logged in on this profile. Please log in manually.")
                print(f"   Waiting 90 seconds for manual login...")
                try:
                    await page.wait_for_url("**/studio.youtube.com**", timeout=90000)
                except Exception:
                    print(f"   ❌ Login timeout. Skipping this profile.")
                    await browser.close()
                    return False

            current_url = page.url
            print(f"   ✅ Logged in! URL: {current_url[:60]}")

            # ── Ir para Customization → Basic Info ───────────────
            print("   📝 Configuring Basic Info...")
            await page.goto(
                "https://studio.youtube.com/channel/UC/customization/basicinfo",
                wait_until="domcontentloaded", timeout=20000
            )
            await asyncio.sleep(3)

            # Channel Name
            try:
                name_selectors = [
                    'input[placeholder*="channel name" i]',
                    '[aria-label*="Name" i] input',
                    'ytcp-social-suggestions-textbox[label="Name"] input',
                    '#channel-name input',
                ]
                for sel in name_selectors:
                    name_field = page.locator(sel).first
                    if await name_field.is_visible(timeout=3000):
                        await name_field.triple_click()
                        await name_field.fill(channel_name)
                        print(f"   ✓ Channel name: {channel_name}")
                        break
            except Exception as e:
                print(f"   ⚠️  Name field not found: {e}")

            # Channel Description
            try:
                desc_selectors = [
                    'textarea[placeholder*="description" i]',
                    '[aria-label*="Description" i] textarea',
                    '#description textarea',
                ]
                for sel in desc_selectors:
                    desc_field = page.locator(sel).first
                    if await desc_field.is_visible(timeout=3000):
                        await desc_field.triple_click()
                        await desc_field.fill(channel_description)
                        print(f"   ✓ Description set ({len(channel_description)} chars)")
                        break
            except Exception as e:
                print(f"   ⚠️  Description field not found: {e}")

            # Save Basic Info
            try:
                save_btn = page.locator('button:has-text("Publish"), button:has-text("Save")').first
                if await save_btn.is_visible(timeout=5000):
                    await save_btn.click()
                    await asyncio.sleep(3)
                    print(f"   ✓ Basic info saved!")
                else:
                    print(f"   ⚠️  Save button not found — may need manual save")
            except Exception as e:
                print(f"   ⚠️  Save failed: {e}")

            # ── Setup OAuth (abre URL de autenticação) ──────────
            print("\n   🔑 Starting YouTube OAuth for API access...")
            await _setup_oauth_with_profile(page, language)

            await asyncio.sleep(3)
            await browser.close()
            print(f"   ✅ Channel setup complete: {channel_name}")
            return True

    except Exception as e:
        print(f"   ❌ Error setting up channel: {e}")
        return False
    finally:
        # Limpa pasta temporária
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


async def _setup_oauth_with_profile(page, language: str) -> None:
    """
    Abre o fluxo OAuth do Google usando o perfil já logado.
    Salva o token automaticamente.
    """
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from pathlib import Path

        client_secrets = Path("credentials/client_secrets.json")
        if not client_secrets.exists():
            print("   ⚠️  client_secrets.json not found. Set up Google Cloud project first.")
            return

        SCOPES = [
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube",
            "https://www.googleapis.com/auth/youtube.force-ssl",
            "https://www.googleapis.com/auth/yt-analytics.readonly",
        ]

        # Gera URL de auth e abre no perfil já logado
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), SCOPES)
        flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"

        auth_url, _ = flow.authorization_url(prompt="consent")
        await page.goto(auth_url)
        await asyncio.sleep(3)

        # Captura o code de autorização (aparece na página após consent)
        print("   ⏳ OAuth consent page opened. Waiting for authorization...")
        # Para fluxo OOB, o code aparece na tela - capturamos via page content
        await asyncio.sleep(8)

        # Tenta ler o code da página
        page_text = await page.inner_text("body")
        if "4/" in page_text:
            # Extrai o code
            import re
            code_match = re.search(r"4/[A-Za-z0-9_\-]+", page_text)
            if code_match:
                code = code_match.group()
                flow.fetch_token(code=code)
                # Salva token
                token_file = f"credentials/youtube_token_{language.replace('-', '_')}.json"
                Path(token_file).parent.mkdir(exist_ok=True)
                with open(token_file, "w") as f:
                    f.write(flow.credentials.to_json())
                print(f"   ✅ OAuth token saved: {token_file}")
    except ImportError:
        print("   ⚠️  google-auth-oauthlib not installed: pip install google-auth-oauthlib")
    except Exception as e:
        print(f"   ⚠️  OAuth setup skipped: {e}")


async def setup_all_channels(assignments: dict) -> None:
    """Configura todos os canais em sequência."""
    profiles = get_chrome_profiles()
    email_to_profile = {p["email"]: p for p in profiles}

    print(f"\n🏭 Dark Channel Factory — Multi-Channel Setup")
    print(f"   Found {len(profiles)} Chrome profiles")
    print(f"   Channels to configure: {len(assignments)}\n")

    results = []
    for email, config in assignments.items():
        profile_info = email_to_profile.get(email)

        if not profile_info:
            print(f"⚠️  {email}: Chrome profile not found (not logged in?)")
            results.append({"email": email, "success": False, "reason": "profile_not_found"})
            continue

        niche = config.get("niche", "ai_tools")
        niche_config = NICHE_CONFIGS.get(niche, NICHE_CONFIGS["ai_tools"])
        description = config.get(
            "channel_description",
            niche_config["description_template"]
        )
        keywords = config.get("channel_keywords", niche_config["keywords"])
        channel_name = config.get("channel_name", f"Dark{niche.replace('_', '').title()}")

        success = await setup_youtube_channel(
            chrome_profile_path=profile_info["path"],
            channel_name=channel_name,
            channel_description=description,
            channel_keywords=keywords,
            niche=niche,
            language=config.get("language", "en-US"),
        )
        results.append({"email": email, "channel": channel_name, "success": success})
        await asyncio.sleep(2)

    # Summary
    print("\n" + "=" * 60)
    print("📊 Setup Summary:")
    for r in results:
        status = "✅" if r["success"] else "❌"
        print(f"  {status} {r['email']} → {r.get('channel', 'N/A')}")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="Dark Channel Factory — Multi-Channel Setup")
    parser.add_argument("--list", action="store_true", help="List all Chrome profiles")
    parser.add_argument("--setup-all", action="store_true", help="Setup all assigned channels")
    parser.add_argument("--profile", help="Chrome profile folder (e.g. 'Profile 1')")
    parser.add_argument("--email", help="Target email to setup")
    parser.add_argument("--niche", default="ai_tools", help="Niche for this channel")
    parser.add_argument("--lang", default="en-US", help="Language (BCP-47)")
    parser.add_argument("--name", help="Channel name")
    args = parser.parse_args()

    if args.list:
        from channel_setup.chrome_profile_mapper import print_profiles
        profiles = print_profiles()
        print("\n💡 To assign channels, edit CHANNEL_ASSIGNMENTS in this script")
        print("   Then run: python channel_setup/multi_channel_setup.py --setup-all\n")

    elif args.setup_all:
        asyncio.run(setup_all_channels(CHANNEL_ASSIGNMENTS))

    elif args.email:
        profiles = get_chrome_profiles()
        profile = next((p for p in profiles if p["email"] == args.email), None)
        if not profile:
            print(f"❌ Profile for {args.email} not found")
            sys.exit(1)
        niche_config = NICHE_CONFIGS.get(args.niche, NICHE_CONFIGS["ai_tools"])
        asyncio.run(setup_youtube_channel(
            chrome_profile_path=profile["path"],
            channel_name=args.name or f"Channel_{args.niche}",
            channel_description=niche_config["description_template"],
            channel_keywords=niche_config["keywords"],
            niche=args.niche,
            language=args.lang,
        ))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
