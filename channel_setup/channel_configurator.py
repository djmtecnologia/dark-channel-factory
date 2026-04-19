"""
Channel Configurator — Dark Channel Factory
Usa Playwright para configurar o perfil do canal YouTube após login MANUAL.

IMPORTANTE: Este script NÃO cria conta Google automaticamente.
Execute APÓS fazer login manual em derikmoretti@gmail.com
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


async def configure_channel(
    channel_name: str = "AIToolsUnlocked",
    channel_description: str | None = None,
    channel_keywords: list[str] | None = None,
    banner_path: str | None = None,
    logo_path: str | None = None,
) -> None:
    """
    Configura o canal YouTube via Playwright (automação do navegador).

    FLUXO:
    1. Abre Chrome com perfil do usuário
    2. Navega para YouTube Studio
    3. Aguarda login manual (se necessário)
    4. Configura nome, descrição, keywords, banner e logo

    Args:
        channel_name: Nome do canal
        channel_description: Descrição do canal
        channel_keywords: Keywords para descoberta
        banner_path: Caminho do arquivo de banner (2560x1440px)
        logo_path: Caminho do arquivo de logo (800x800px)
    """
    from playwright.async_api import async_playwright

    default_description = """Welcome to AIToolsUnlocked — your #1 source for AI tools, automation strategies, and business productivity hacks.

🚀 We publish 3 videos per week covering:
• The latest AI tools for business
• Step-by-step automation workflows
• Real ROI comparisons and honest reviews
• How to replace manual work with AI

Subscribe and turn on notifications to stay ahead of the AI revolution!

📧 Business inquiries: contact@aitoolsunlocked.com
"""

    default_keywords = [
        "AI tools", "artificial intelligence", "business automation",
        "productivity", "ChatGPT", "AI for business", "automation tools",
        "make money with AI", "AI productivity", "best AI tools 2025"
    ]

    description = channel_description or default_description
    keywords = channel_keywords or default_keywords

    print(f"\n🎬 Channel Configurator — AIToolsUnlocked")
    print(f"=" * 50)
    print(f"Channel Name: {channel_name}")
    print(f"Account: derikmoretti@gmail.com")
    print(f"\n⚠️  IMPORTANT: Please log in manually when the browser opens.")
    print(f"The script will wait for you to be logged in.\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--start-maximized"],
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
        )
        page = await context.new_page()

        # ── Step 1: Navegar para YouTube Studio ───────────────
        print("📍 Opening YouTube Studio...")
        await page.goto("https://studio.youtube.com", wait_until="networkidle")
        await asyncio.sleep(3)

        # Verifica se precisa de login
        if "accounts.google.com" in page.url or "signin" in page.url:
            print("\n⚠️  LOGIN REQUIRED!")
            print("Please log in with derikmoretti@gmail.com in the browser that opened.")
            print("After logging in, the script will continue automatically.")
            print("Waiting up to 120 seconds for login...\n")

            # Aguarda redirecionamento para Studio
            await page.wait_for_url("**/studio.youtube.com**", timeout=120000)
            print("✅ Login detected! Continuing...\n")

        await asyncio.sleep(3)

        # ── Step 2: Ir para Customization ─────────────────────
        print("📍 Navigating to Channel Customization...")
        await page.goto(
            "https://studio.youtube.com/channel/UC/customization/basicinfo",
            wait_until="networkidle"
        )
        await asyncio.sleep(2)

        # ── Step 3: Configurar Nome do Canal ──────────────────
        try:
            name_field = page.locator('[aria-label="Name"]').first
            if await name_field.is_visible():
                await name_field.triple_click()
                await name_field.fill(channel_name)
                print(f"✅ Channel name set: {channel_name}")
        except Exception as e:
            print(f"⚠️  Could not set channel name: {e}")

        # ── Step 4: Configurar Descrição ──────────────────────
        try:
            desc_field = page.locator('[aria-label="Description"]').first
            if await desc_field.is_visible():
                await desc_field.triple_click()
                await desc_field.fill(description)
                print(f"✅ Description set ({len(description)} chars)")
        except Exception as e:
            print(f"⚠️  Could not set description: {e}")

        # ── Step 5: Configurar Keywords ───────────────────────
        try:
            keywords_str = ", ".join(keywords[:10])
            kw_field = page.locator('[aria-label*="keyword" i], [placeholder*="keyword" i]').first
            if await kw_field.is_visible():
                await kw_field.fill(keywords_str)
                print(f"✅ Keywords set: {keywords_str[:60]}...")
        except Exception as e:
            print(f"⚠️  Could not set keywords: {e}")

        # ── Step 6: Salvar Basic Info ──────────────────────────
        try:
            save_btn = page.locator('button:has-text("Publish"), button:has-text("Save")').first
            if await save_btn.is_visible():
                await save_btn.click()
                await asyncio.sleep(2)
                print("✅ Basic info saved!")
        except Exception as e:
            print(f"⚠️  Could not save: {e}")

        # ── Step 7: Configurar Logo/Avatar ────────────────────
        if logo_path and Path(logo_path).exists():
            try:
                await page.goto(
                    "https://studio.youtube.com/channel/UC/customization/basicinfo",
                    wait_until="networkidle"
                )
                upload_btn = page.locator('[aria-label*="profile photo" i], [aria-label*="channel icon" i]').first
                if await upload_btn.is_visible():
                    async with page.expect_file_chooser() as fc_info:
                        await upload_btn.click()
                    file_chooser = await fc_info.value
                    await file_chooser.set_files(logo_path)
                    await asyncio.sleep(3)
                    print(f"✅ Logo uploaded: {logo_path}")
            except Exception as e:
                print(f"⚠️  Logo upload failed: {e}")

        # ── Step 8: Configurar Banner ─────────────────────────
        if banner_path and Path(banner_path).exists():
            try:
                await page.goto(
                    "https://studio.youtube.com/channel/UC/customization/branding",
                    wait_until="networkidle"
                )
                banner_btn = page.locator('[aria-label*="banner" i]').first
                if await banner_btn.is_visible():
                    async with page.expect_file_chooser() as fc_info:
                        await banner_btn.click()
                    file_chooser = await fc_info.value
                    await file_chooser.set_files(banner_path)
                    await asyncio.sleep(3)
                    print(f"✅ Banner uploaded: {banner_path}")
            except Exception as e:
                print(f"⚠️  Banner upload failed: {e}")

        print("\n🎉 Channel configuration complete!")
        print(f"📺 Check your channel: https://www.youtube.com/account_customization")

        await asyncio.sleep(5)
        await browser.close()


def run_channel_setup(
    channel_name: str = "AIToolsUnlocked",
    banner_path: str | None = None,
    logo_path: str | None = None,
) -> None:
    """Entry point síncrono para setup do canal."""
    asyncio.run(configure_channel(
        channel_name=channel_name,
        banner_path=banner_path,
        logo_path=logo_path,
    ))


if __name__ == "__main__":
    run_channel_setup()
