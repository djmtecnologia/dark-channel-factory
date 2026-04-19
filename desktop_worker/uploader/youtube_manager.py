import os
import asyncio
from playwright.async_api import async_playwright

async def upload_to_youtube(video_path: str, metadata: dict, profile_path: str) -> str:
    """
    Usa o Playwright para fazer upload do vídeo via navegador.
    Aproveita o perfil do Chrome já logado para evitar 2FA e bloqueios.
    """
    print(f"🚀 [Uploader] Iniciando upload: {video_path}")
    
    async with async_playwright() as p:
        # Abre o navegador com o perfil específico
        context = await p.chromium.launch_persistent_context(
            user_data_dir=profile_path,
            headless=False, # Manter visível para validação inicial
            channel="chrome",
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"]
        )
        
        page = context.pages[0] if context.pages else await context.new_page()
        
        try:
            # 1. Navegar para o YouTube Studio
            await page.goto("https://studio.youtube.com", timeout=60000)
            
            # 2. Clicar em CREATE -> Upload videos
            await page.click("#create-icon")
            await page.click("#text-item-0") # Upload videos
            
            # 3. Selecionar arquivo
            async with page.expect_file_chooser() as fc_info:
                await page.click("#select-files-button")
            file_chooser = await fc_info.value
            await file_chooser.set_files(video_path)
            
            # 4. Preencher Metadados (Título/Descrição)
            # Aguarda a tela de detalhes carregar
            await page.wait_for_selector("#title-textarea", timeout=30000)
            
            title = metadata.get("title", "New Video")
            description = metadata.get("description", "")
            
            # Título
            await page.fill("#title-textarea #textbox", title)
            # Descrição
            await page.fill("#description-textarea #textbox", description)
            
            # 5. Avançar até o fim (Simplificado para o MVP)
            for _ in range(3):
                await page.click("#next-button")
                await asyncio.sleep(2)
            
            # 6. Publicar (Público)
            await page.click("#done-button")
            await asyncio.sleep(5)
            
            print(f"✅ [Uploader] Upload concluído para: {title}")
            await context.close()
            return "SUCCESS"
            
        except Exception as e:
            print(f"❌ [Uploader] Falha no upload: {e}")
            await context.close()
            raise e
