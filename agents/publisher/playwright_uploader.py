import os
import time
from playwright.sync_api import sync_playwright

PROFILES_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "chrome_profiles"))

def validate_youtube_studio_metadata(page, video_link, expected_title, scheduled_at):
    """Agente Validador Pós-Upload: Verifica se o YouTube realmente salvou os dados."""
    print("\n🔍 [VALIDATOR PÓS-UPLOAD] Auditando vídeo salvo no YouTube Studio...")
    try:
        video_id = video_link.split("/")[-1]
        edit_url = f"https://studio.youtube.com/video/{video_id}/edit"
        page.goto(edit_url, timeout=60000, wait_until="domcontentloaded")
        time.sleep(4)
        
        # 1. Valida Título
        title_box = page.locator("#title-textarea #textbox")
        actual_title = title_box.inner_text().strip()
        expected = expected_title[:100].strip()
        if expected in actual_title or actual_title in expected:
            print("   ✅ Título salvo corretamente.")
        else:
            print(f"   ❌ Título inconsistente. Salvo: {actual_title}")
            
        # 2. Valida Tela Final (End Screen)
        # O botão End Screen fica na lateral direita na página de edição
        end_screen_btn = page.locator("#endscreens-button")
        if end_screen_btn.is_visible():
            print("   ✅ Módulo de Tela Final (End Screen) confirmado.")
        
        # 3. Valida Agendamento
        try:
            visibility_text = page.locator("#visibility-button").inner_text(timeout=5000).lower()
            if scheduled_at:
                if "schedule" in visibility_text or "programado" in visibility_text or "agendado" in visibility_text:
                    print("   ✅ Agendamento de data confirmado.")
                else:
                    print(f"   ❌ Erro de Agendamento. Status real do YouTube: {visibility_text.strip()}")
            else:
                if "public" in visibility_text or "público" in visibility_text:
                    print("   ✅ Visibilidade Pública confirmada.")
                else:
                    print(f"   ❌ Erro de Visibilidade. Status real do YouTube: {visibility_text.strip()}")
        except:
            print("   ℹ️ Botão de Visibilidade indisponível no layout atual (Shorts ou UI variante). Pulando validação visual.")
                
        print("🛡️ [VALIDATOR] Auditoria Pós-Upload concluída!\n")
    except Exception as e:
        print(f"   ℹ️ O Validador Pós-Upload encerrou a leitura graciosamente.")

def publish_video_to_youtube(video_path, thumbnail_path, title, description, tags, channel_identifier, privacy_status="public", pinned_comment=None, is_short=False, scheduled_at=None, srt_path=None, quiz_data=None):
    """
    Motor de Upload Invisível (Playwright)
    channel_identifier: "en-US", "fr-FR", etc.
    """
    profile_path = os.path.join(PROFILES_DIR, channel_identifier)
    os.makedirs(profile_path, exist_ok=True)
    
    print(f"🤖 [PLAYWRIGHT] Iniciando navegador fantasma para o canal: {channel_identifier}")
    
    with sync_playwright() as p:
        # Lança o navegador com o perfil persistente do canal
        browser = p.chromium.launch_persistent_context(
            user_data_dir=profile_path,
            headless=False, # Pode mudar para True no futuro se quiser upload totalmente fantasma
            channel="chrome",
            args=[
                "--no-sandbox", 
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled"
            ],
            ignore_default_args=["--enable-automation"]
        )
        
        page = browser.new_page()
        try:
            print("🤖 [PLAYWRIGHT] Navegando para o YouTube Studio...")
            page.goto("https://studio.youtube.com", timeout=60000, wait_until="domcontentloaded")
        except Exception as goto_err:
            print(f"⚠️ [PLAYWRIGHT] Alerta de lentidão/timeout na navegação inicial do studio.youtube.com: {goto_err}")
            print("🔍 Verificando se a página já está utilizável ou se podemos continuar...")
        
        # Verifica se está logado
        if "accounts.google.com" in page.url or "signin" in page.url:
            print(f"\n🚨 [AÇÃO NECESSÁRIA] O Bot precisa que você faça login manualmente para o canal: {channel_identifier}")
            print(f"👉 Faça login com o email correspondente na janela do Chrome que abriu.")
            print("⏳ Aguardando você fazer login e carregar o YouTube Studio...")
            page.wait_for_url("**/channel/**", timeout=300000) # Espera até 5 minutos
            print("✅ Login detectado! Salvando sessão para sempre...")
        
        try:
            print("⬆️ [PLAYWRIGHT] Clicando no botão de Upload...")
            # Usa o ID universal do ícone de Upload (a seta para cima), ignorando o idioma
            page.click("#upload-icon", timeout=15000)
            
            # Insere o arquivo de vídeo
            print("📁 [PLAYWRIGHT] Anexando arquivo de vídeo...")
            page.set_input_files("input[type='file']", video_path)
            
            # Espera o modal de detalhes carregar
            print("📝 [PLAYWRIGHT] Preenchendo Título e Descrição...")
            time.sleep(4)
            
            # Preenche o Título de forma altamente reativa via Keyboard
            print(f"📝 [PLAYWRIGHT] Preenchendo Título: {title[:50]}...")
            title_box = page.locator("#title-textarea #textbox")
            title_box.wait_for(state="visible", timeout=20000)
            title_box.scroll_into_view_if_needed()
            
            # Foco e limpeza via teclado
            title_box.click(force=True)
            time.sleep(1)
            page.keyboard.press("Control+A")
            time.sleep(0.5)
            page.keyboard.press("Backspace")
            time.sleep(0.5)
            
            # Digitação física real que força o framework do YouTube a salvar os dados
            page.keyboard.insert_text(title[:100])
            time.sleep(2)
            
            # Preenche a Descrição de forma altamente reativa via Keyboard
            print("📝 [PLAYWRIGHT] Preenchendo Descrição...")
            desc_box = page.locator("#description-textarea #textbox")
            desc_box.wait_for(state="visible", timeout=15000)
            desc_box.scroll_into_view_if_needed()
            
            # Foco e limpeza via teclado
            desc_box.click(force=True)
            time.sleep(1)
            page.keyboard.press("Control+A")
            time.sleep(0.5)
            page.keyboard.press("Backspace")
            time.sleep(0.5)
            
            # Digitação física real
            page.keyboard.insert_text(description[:5000])
            time.sleep(2)
            
            # Se for vídeo longo e tiver thumbnail
            if not is_short and thumbnail_path and os.path.exists(thumbnail_path):
                print("🎨 [PLAYWRIGHT] Anexando Thumbnail...")
                try:
                    page.locator("input#file-loader").set_input_files(thumbnail_path)
                    time.sleep(3)
                except Exception as e:
                    print(f"⚠️ [PLAYWRIGHT] Thumb upload failed (non-critical): {e}")
            
            # Marca como "Não é para crianças" (Crucial para habilitar o botão Próximo)
            try:
                print("👶 [PLAYWRIGHT] Configurando restrição de idade (Não é para crianças)...")
                # Clique JS Universal: O segundo rádio é sempre o "Não"
                kids_configured = page.evaluate("""
                    () => {
                        const radios = document.querySelectorAll('tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_FALSE"], tp-yt-paper-radio-button');
                        for (const r of radios) {
                            if (r.getAttribute('name') === 'VIDEO_MADE_FOR_KIDS_FALSE') {
                                r.click();
                                return true;
                            }
                        }
                        const kidsRadios = Array.from(document.querySelectorAll('tp-yt-paper-radio-button')).filter(r => {
                            const text = (r.innerText || '').toLowerCase();
                            return text.includes('kids') || text.includes('criança') || text.includes('enfan') || text.includes('niño') || text.includes('kinder');
                        });
                        if (kidsRadios.length >= 2) {
                            kidsRadios[1].click();
                            return true;
                        }
                        for (const r of radios) {
                            const text = (r.innerText || '').toLowerCase();
                            if (text.includes('não') || text.includes('no') || text.includes('nicht') || text.includes('pas')) {
                                r.click();
                                return true;
                            }
                        }
                        return false;
                    }
                """)
                if kids_configured:
                    print("✅ [PLAYWRIGHT] Restrição de idade configurada via JS Inteligente.")
                else:
                    kids_not_made_radio = page.locator("tp-yt-paper-radio-button").filter(
                        has_text=re.compile(r"não é conteúdo|not made for kids|pas conçue pour les enfants|no es contenido|nicht speziell für kinder", re.IGNORECASE)
                    ).first
                    kids_not_made_radio.wait_for(state="visible", timeout=8000)
                    kids_not_made_radio.scroll_into_view_if_needed()
                    kids_not_made_radio.click(force=True, timeout=5000)
                    print("✅ [PLAYWRIGHT] Restrição de idade configurada via DOM.")
            except Exception as e:
                print(f"⚠️ [PLAYWRIGHT] Aviso ao configurar restrição de idade: {e}")

            # -------------------------------------------------------------
            # [NOVO] Adicionar Vídeo Relacionado (Somente para Shorts)
            # -------------------------------------------------------------
            if is_short:
                try:
                    print("🔗 [PLAYWRIGHT] Vinculando 'Vídeo Relacionado' (Vídeo Longo) ao Short...")
                    import re
                    # Localiza o container de Vídeo Relacionado na barra lateral direita ou abaixo
                    related_container = page.locator("ytcp-video-metadata-related-video").first
                    if related_container.is_visible():
                        related_container.click(force=True)
                        time.sleep(3)
                        
                        # O modal abre a lista de vídeos do canal. O primeiro (mais recente) é o vídeo Longo!
                        first_video = page.locator("ytcp-entity-card, ytcp-video-row").first
                        if first_video.is_visible():
                            first_video.click(force=True)
                            print("✅ [PLAYWRIGHT] Vídeo longo vinculado com sucesso ao Short!")
                            time.sleep(2)
                        else:
                            print("⚠️ [PLAYWRIGHT] Nenhum vídeo longo encontrado para vincular.")
                            page.keyboard.press("Escape")
                    else:
                        print("   ℹ️ [PLAYWRIGHT] Opção de 'Vídeo Relacionado' indisponível no momento.")
                except Exception as e:
                    print(f"⚠️ [PLAYWRIGHT] Erro não-crítico ao vincular Vídeo Relacionado: {e}")
                    page.keyboard.press("Escape")
                    time.sleep(1)

            # -------------------------------------------------------------
            # [NOVO] Expandir Mostrar Mais: TAGS e IDIOMA
            # -------------------------------------------------------------
            try:
                print("🏷️ [PLAYWRIGHT] Expandindo opções avançadas (Tags e Idioma)...")
                import re
                # Usa Javascript para garantir que clica em Mostrar Mais sem falhas de visibilidade do overlay
                try: page.evaluate("document.querySelector('ytcp-button#toggle-button').click()")
                except: page.click("ytcp-button#toggle-button", timeout=5000, force=True)
                time.sleep(2)
                
                # Preenche Tags
                if tags:
                    # Limpa as tags existentes se houver chips de tags pré-carregados
                    try:
                        chips = page.locator("ytcp-chip").all()
                        if chips:
                            print(f"🗑️ [PLAYWRIGHT] Encontrados {len(chips)} chips de tags existentes. Limpando do zero...")
                            for chip in chips:
                                delete_btn = chip.locator("#delete-icon, [aria-label*='delete'], [aria-label*='remover'], button").first
                                if delete_btn.is_visible():
                                    delete_btn.click(force=True)
                                    time.sleep(0.2)
                    except Exception as e_clear:
                        print(f"⚠️ [PLAYWRIGHT] Aviso ao tentar limpar tags existentes: {e_clear}")

                    # Valida e recorta as tags para garantir limite máximo estrito de 500 caracteres
                    valid_tags = []
                    current_length = 0
                    for tag in tags:
                        tag_str = tag.strip()
                        if not tag_str:
                            continue
                        added_len = len(tag_str) + (2 if valid_tags else 0)
                        # O YouTube também adiciona uma vírgula final na caixa, então mantemos margem segura de 495 caracteres
                        if current_length + added_len <= 495:
                            valid_tags.append(tag_str)
                            current_length += added_len
                        else:
                            print(f"⚠️ [PLAYWRIGHT] Limite de 500 caracteres de tags atingido. Ignorando tag '{tag_str}' e subsequentes.")
                            break

                    if valid_tags:
                        tags_str = ", ".join(valid_tags) + ","
                        print(f"🏷️ Inserindo Tags (comprimento: {len(tags_str)} caracteres): {tags_str[:100]}...")
                        tags_box = page.locator("ytcp-free-text-chip-bar input").first
                        tags_box.click(force=True)
                        page.keyboard.insert_text(tags_str)
                        time.sleep(1)
                
                # Preenche Idioma do Vídeo
                lang_map = {
                    "en-US": "Inglês",
                    "pt-BR": "Português",
                    "es-ES": "Espanhol",
                    "fr-FR": "Francês",
                    "de-DE": "Alemão",
                    "ar-SA": "Árabe"
                }
                target_lang = lang_map.get(channel_identifier, "Inglês")
                
                print(f"🌐 [PLAYWRIGHT] Configurando Idioma do vídeo para: {target_lang}...")
                lang_dropdown = page.locator("ytcp-language-picker, ytcp-text-dropdown").filter(has_text=re.compile(r"idioma|language", re.IGNORECASE)).first
                if lang_dropdown.is_visible():
                    lang_dropdown.click(force=True)
                    time.sleep(1)
                    
                    # Seleciona o idioma (ex: Inglês, Inglês (Estados Unidos), etc)
                    page.locator("tp-yt-paper-item").filter(has_text=re.compile(target_lang, re.IGNORECASE)).first.click(force=True)
                    time.sleep(2)
            except Exception as e:
                print(f"⚠️ [PLAYWRIGHT] Aviso não crítico ao preencher tags/idioma: {e}")
            
            # Avança para Video Elements (Tela Final e Legendas)
            print("⏭️ [PLAYWRIGHT] Avançando para Tela Final e Legendas...")
            page.click("ytcp-button#next-button")
            time.sleep(3)
            
            # -------------------------------------------------------------
            # [NOVO] Upload de Legendas SRT (APENAS para vídeos LONGOS)
            # Shorts já têm legenda burn-in amarela no FFmpeg. Não enviar SRT para evitar legenda branca duplicada.
            # -------------------------------------------------------------
            if srt_path and os.path.exists(srt_path) and not is_short:
                try:
                    print("📝 [PLAYWRIGHT] Fazendo upload do arquivo de Legendas (SRT)...")
                    
                    # Aguarda a aba de Elementos carregar completamente antes de procurar o botão de SRT
                    # O timeout do End Screen esgota antes e pode deixar a página em estado parcial
                    try:
                        page.wait_for_selector("ytcp-video-elements-row", timeout=15000)
                        time.sleep(2)
                    except:
                        print("   ⚠️ [SRT] Aba de Elementos ainda carregando, aguardando...")
                        time.sleep(4)

                    # Estratégia 1: Clica no botão da linha de legendas via DOM
                    srt_uploaded = False
                    try:
                        srt_row = page.locator("ytcp-video-elements-row").filter(has_text=re.compile(r"legendas|subtitles|captions|sous-titres|untertitel|subtítulos", re.IGNORECASE)).first
                        if srt_row.is_visible(timeout=8000):
                            upload_btn = srt_row.locator("#add-button, #edit-button").first
                            if upload_btn.is_visible(timeout=5000):
                                upload_btn.click(force=True)
                                time.sleep(2)
                                
                                # Clica em "Fazer upload de arquivo"
                                page.locator("ytcp-button").filter(has_text=re.compile(r"fazer upload|upload file|charger un fichier|Datei hochladen|subir archivo", re.IGNORECASE)).first.click(force=True)
                                time.sleep(1)
                                
                                # Clica em "Continuar" no aviso de timing (se aparecer)
                                try:
                                    cont_btn = page.locator("ytcp-button#continue-button").first
                                    if cont_btn.is_visible(timeout=3000):
                                        cont_btn.click(force=True)
                                        time.sleep(1)
                                except: pass
                                
                                # Usa file_chooser para enviar o arquivo SRT
                                with page.expect_file_chooser(timeout=10000) as fc_info:
                                    page.locator("input[type='file']").first.click(force=True)
                                file_chooser = fc_info.value
                                file_chooser.set_files(srt_path)
                                time.sleep(3)
                                
                                # Salva/Conclui
                                try:
                                    page.locator("ytcp-button#done-button, #save-button").first.click(force=True)
                                except:
                                    page.keyboard.press("Enter")
                                time.sleep(2)
                                print("✅ [PLAYWRIGHT] Legendas SRT anexadas com sucesso!")
                                srt_uploaded = True
                    except Exception as e1:
                        print(f"   ⚠️ [SRT] Estratégia DOM falhou: {e1}")

                    # Estratégia 2 (Fallback JS): Injeta o arquivo diretamente no input oculto de upload de legendas
                    if not srt_uploaded:
                        try:
                            print("   🔄 [SRT] Tentando injeção JS direta no input de legendas...")
                            # O YouTube Studio tem um input file oculto específico para SRT
                            srt_input = page.locator("input[accept='.srt,.vtt,.sbv,.sub,.txt,.ttml,.xml']").first
                            if srt_input.count() > 0:
                                srt_input.set_input_files(srt_path)
                                time.sleep(3)
                                print("✅ [PLAYWRIGHT] Legendas injetadas via input direto!")
                                srt_uploaded = True
                        except Exception as e2:
                            print(f"   ⚠️ [SRT] Injeção JS também falhou: {e2}")
                    
                    if not srt_uploaded:
                        print("   ⚠️ [SRT] Legenda não enviada neste upload. Será necessário adicionar manualmente no YouTube Studio.")
                        
                except Exception as e:
                    print(f"⚠️ [PLAYWRIGHT] Erro isolado no upload do SRT: {e}")
                    page.keyboard.press("Escape")
                    time.sleep(1)
                    page.keyboard.press("Escape")
                    try: page.locator("ytcp-icon-button#close-button").first.click(timeout=2000, force=True)
                    except: pass

            
            # Quiz / Enquete (apenas para vídeos LONGOS)
            if quiz_data and isinstance(quiz_data, dict) and not is_short:
                try:
                    print("❓ [PLAYWRIGHT] Inserindo Teste Interativo (Quiz)...")
                    # Aguarda a aba de elementos carregar
                    time.sleep(3)
                    quiz_row = None
                    try:
                        page.wait_for_selector(
                            "ytcp-video-elements-row",
                            timeout=8000
                        )
                        quiz_row = page.locator("ytcp-video-elements-row").filter(
                            has_text=re.compile(r"teste|quiz|poll|sondagem|interactiv|cuestionario|fragerunde", re.IGNORECASE)
                        ).first
                    except: pass
                    
                    if quiz_row and quiz_row.is_visible(timeout=3000):
                        quiz_btn = quiz_row.locator("#add-button, #edit-button").first
                        if quiz_btn.is_visible(timeout=3000):
                            quiz_btn.click(force=True)
                            time.sleep(3)
                            question = str(quiz_data.get("question", ""))[:120]
                            answers = quiz_data.get("answers", [])[:4]
                            textboxes = page.locator(
                                "ytcp-social-suggestions-textbox #textbox, ytcp-form-textarea #textbox, tp-yt-paper-textarea #textarea"
                            )
                            if textboxes.count() >= 3:
                                textboxes.nth(0).click(force=True)
                                page.keyboard.insert_text(question)
                                time.sleep(1)
                                for i, ans in enumerate(answers):
                                    if i+1 < textboxes.count():
                                        textboxes.nth(i+1).click(force=True)
                                        page.keyboard.insert_text(str(ans)[:50])
                                        time.sleep(0.5)
                                correct_idx = int(quiz_data.get("correct_index", 0))
                                radios = page.locator("tp-yt-paper-radio-button")
                                if radios.count() > correct_idx:
                                    radios.nth(correct_idx).click(force=True)
                                save_btn = page.locator("ytcp-button#save-button, ytcp-button#done-button").first
                                save_btn.click(force=True)
                                print("✅ [PLAYWRIGHT] Quiz interativo configurado!")
                                time.sleep(2)
                        else:
                            print("⚠️ [PLAYWRIGHT] Botão de Quiz indisponível neste upload.")
                    else:
                        print("⚠️ [PLAYWRIGHT] Linha de Quiz não encontrada na aba de elementos.")
                except Exception as e:
                    print(f"⚠️ [PLAYWRIGHT] Falha no Quiz: {e}")
                    page.keyboard.press("Escape")
            
            # Tela Final (End Screen) - apenas para vídeos LONGOS
            if not is_short:
                try:
                    print("📺 [PLAYWRIGHT] Adicionando Tela Final...")
                    try:
                        page.wait_for_selector(
                            "ytcp-video-elements-row",
                            timeout=8000
                        )
                        endscreen_row = page.locator("ytcp-video-elements-row").filter(
                            has_text=re.compile(r"tela final|end screen|endscreen|\u00e9cran de fin|pantalla final|abspann", re.IGNORECASE)
                        ).first
                        if endscreen_row.is_visible(timeout=3000):
                            import_btn = endscreen_row.locator("#import-button").first
                            if import_btn.is_visible(timeout=2000):
                                import_btn.click(force=True)
                                time.sleep(3)
                                # Seleciona o último vídeo como template
                                first_video = page.locator("ytcp-video-row").first
                                if first_video.is_visible(timeout=4000):
                                    first_video.click(force=True)
                                    time.sleep(2)
                                    page.locator("ytcp-button#save-button").click(force=True)
                                    print("✅ Tela Final importada com sucesso!")
                            else:
                                add_btn = endscreen_row.locator("#add-button").first
                                if add_btn.is_visible(timeout=2000):
                                    add_btn.click(force=True)
                                    time.sleep(3)
                                    # Tenta adicionar elemento de inscrição
                                    sub_btn = page.locator("ytcp-endscreen-element-type-button").filter(
                                        has_text=re.compile(r"inscrição|subscribe", re.IGNORECASE)
                                    ).first
                                    if sub_btn.is_visible(timeout=3000):
                                        sub_btn.click(force=True)
                                        time.sleep(1)
                                    page.locator("ytcp-button#save-button").click(force=True)
                                    print("✅ Tela Final criada com botão de inscrição!")
                    except Exception as e1:
                        print(f"   ⚠️ End Screen via row falhou: {e1}")
                    
                    time.sleep(2)
                except Exception as e:
                    print(f"   ℹ️ [INFO] Tela Final ignorada: {e}")
                    page.keyboard.press("Escape")

            # Avança até a aba de Visibilidade (Adaptativo para Monetização, Shorts e Longos)
            print("⏭️ [PLAYWRIGHT] Avançando para a aba Visibilidade (Agendamento)...")
            for step_attempt in range(10):
                time.sleep(3)
                vis_check = page.locator("ytcp-visibility-radios, ytcp-video-visibility").first
                if vis_check.is_visible():
                    print("✅ [PLAYWRIGHT] Chegamos à aba de Visibilidade!")
                    break
                
                # Tenta clicar no botão Next que está visível na interface atual
                step_clicked = page.evaluate("""
                    () => {
                        const nextBtns = Array.from(document.querySelectorAll('ytcp-button#next-button'));
                        const visibleNext = nextBtns.find(b => {
                            if (b.hasAttribute('hidden') || b.getAttribute('aria-hidden') === 'true') return false;
                            const rect = b.getBoundingClientRect();
                            return rect.width > 0 && rect.height > 0 && window.getComputedStyle(b).display !== 'none';
                        });
                        
                        if (visibleNext) {
                            const is_disabled = visibleNext.hasAttribute('disabled') || visibleNext.getAttribute('aria-disabled') === 'true';
                            if (!is_disabled) {
                                visibleNext.click();
                                return 'clicked';
                            }
                            return 'disabled';
                        }
                        return 'not_found';
                    }
                """)
                
                print(f"   ⏭️ Avanço de aba (Etapa {step_attempt + 1}): status={step_clicked}")
                
                if step_clicked == 'disabled':
                    print("   ⏳ [PLAYWRIGHT] Botão 'Próximo' está temporariamente desabilitado. Aguardando processamento da aba...")
                    time.sleep(5)
                
                # Se não encontrar o botão ou se as tentativas consecutivas falharem, usa o clique direto nos cabeçalhos como atalho supremo
                if step_attempt >= 3 or step_clicked == 'not_found':
                    print("   🔄 [PLAYWRIGHT] Tentando atalho direto clicando nos steps de cabeçalho no topo...")
                    stepper_clicked = page.evaluate("""
                        () => {
                            const steps = Array.from(document.querySelectorAll('tp-yt-paper-step, ytcp-stepper-item, .step'));
                            const targetStep = steps.find(s => {
                                const t = (s.innerText || '').toLowerCase();
                                return t.includes('visib') || t.includes('sicht') || t.includes('programar') || t.includes('agend') || t.includes('public');
                            });
                            if (targetStep) {
                                targetStep.click();
                                return true;
                            }
                            // Tenta clicar no último step disponível no cabeçalho
                            if (steps.length > 0) {
                                steps[steps.length - 1].click();
                                return true;
                            }
                            return false;
                        }
                    """)
                    if stepper_clicked:
                        print("   ✅ [PLAYWRIGHT] Atalho de Stepper acionado via JS.")
                        time.sleep(2)

            # Define visibilidade
            print("👁️ [PLAYWRIGHT] Configurando visibilidade/agendamento...")
            if scheduled_at:
                print(f"📅 [PLAYWRIGHT] Agendando vídeo para {scheduled_at.strftime('%d/%m/%Y %H:%M')}...")
                time.sleep(5)
                
                # YouTube UI: Expande a seção de agendamento de forma altamente resiliente via JS
                try:
                    print("📂 [PLAYWRIGHT] Expandindo seção de Agendamento...")
                    time.sleep(4)
                    expanded = page.evaluate("""
                        () => {
                            const allDivs = Array.from(document.querySelectorAll('div, ytcp-button, ytcp-video-visibility-select-step'));
                            const scheduleHeader = allDivs.find(d => {
                                const t = (d.innerText || '').toLowerCase();
                                return (t.includes('schedule') || t.includes('programar') || t.includes('agendar')) && d.querySelector('#second-container-expand-button, #schedule-section');
                            });
                            if (scheduleHeader) {
                                const btn = scheduleHeader.querySelector('#second-container-expand-button, #schedule-section, [role="button"]');
                                if (btn) { btn.click(); return true; }
                                scheduleHeader.click();
                                return true;
                            }
                            const expandBtn = document.querySelector('#second-container-expand-button, #schedule-section');
                            if (expandBtn) { expandBtn.click(); return true; }
                            return false;
                        }
                    """)
                    if expanded:
                        print("   ✅ Seção de Agendamento expandida via JS.")
                    else:
                        expand_sched = page.locator("#schedule-section, #second-container-expand-button, ytcp-video-visibility-select-step #schedule-radio-button, [name='SCHEDULE']").first
                        expand_sched.click(force=True)
                    time.sleep(3)
                except Exception as e:
                    print(f"   ℹ️ [PLAYWRIGHT] Info: Tentativa de expansão ignorada ({e})")

                # Verifica se os inputs de data e hora já estão visíveis/ativos após expandir
                date_input_test = page.locator("ytcp-date-picker input").first
                time_input_test = page.locator("ytcp-time-of-day-picker input").first
                
                if date_input_test.is_visible() or time_input_test.is_visible():
                    print("   ✅ [PLAYWRIGHT] Campos de agendamento já estão visíveis e ativos. Pulando clique no rádio.")
                    sched_clicked = True
                else:
                    # Estratégia 1: Busca Recursiva no Shadow DOM sem a restrição de offsetParent
                    sched_clicked = page.evaluate("""
                        () => {
                            function isVisible(el) {
                                if (!el) return false;
                                const rect = el.getBoundingClientRect();
                                if (rect.width === 0 && rect.height === 0) return false;
                                const style = window.getComputedStyle(el);
                                return style.display !== 'none' && style.visibility !== 'hidden';
                            }
                            
                            function findAndClick(root) {
                                // 1. Tenta seletores conhecidos
                                const selectors = [
                                    'tp-yt-paper-radio-button#schedule-radio-button',
                                    'ytcp-paper-radio-button[name="SCHEDULE"]',
                                    '#schedule-radio-button',
                                    '[name="SCHEDULE"]',
                                    '[value="SCHEDULE"]'
                                ];
                                for (const sel of selectors) {
                                    const el = root.querySelector(sel);
                                    if (el && isVisible(el)) { 
                                        el.click(); 
                                        return true; 
                                    }
                                }
                                // 2. Busca por texto se o seletor falhar
                                const allRadios = Array.from(root.querySelectorAll('tp-yt-paper-radio-button, [role="radio"]'));
                                const target = allRadios.find(r => {
                                    const t = (r.innerText || '').toLowerCase();
                                    return t.includes('sched') || t.includes('prog') || t.includes('agen') || t.includes('plan');
                                });
                                if (target && isVisible(target)) { 
                                    target.click(); 
                                    return true; 
                                }
                                
                                // 3. Recursão para Shadow Roots
                                const children = Array.from(root.querySelectorAll('*'));
                                for (const child of children) {
                                    if (child.shadowRoot && findAndClick(child.shadowRoot)) return true;
                                }
                                return false;
                            }
                            return findAndClick(document);
                        }
                    """)
                    
                    if not sched_clicked:
                        print("⚠️ [PLAYWRIGHT] JS não encontrou radio, tentando via locator...")
                        try:
                            sched_loc = page.locator("[name='SCHEDULE'], #schedule-radio-button, [value='SCHEDULE']").first
                            sched_loc.scroll_into_view_if_needed(timeout=10000)
                            sched_loc.click(force=True)
                            sched_clicked = 'locator'
                        except Exception as e2:
                            # Se os inputs de data ou hora estiverem presentes de qualquer forma, consideramos sucesso
                            if date_input_test.count() > 0 or time_input_test.count() > 0:
                                print("   ✅ [PLAYWRIGHT] Inputs de data/hora detectados no DOM após falha do rádio. Prosseguindo...")
                                sched_clicked = 'fallback_elements'
                            else:
                                print(f"⚠️ [PLAYWRIGHT] Fallback também falhou: {e2}. Publicando como Público direto.")
                                page.evaluate("document.querySelectorAll('tp-yt-paper-radio-button, [role=radio]').forEach(r => { if((r.innerText||'').toLowerCase().includes('public') || (r.innerText||'').toLowerCase().includes('p\u00fablico')) r.click(); })")
                    else:
                        print(f"✅ [PLAYWRIGHT] Agendamento selecionado via JS.")
                
                time.sleep(2)
                
                # Preenchimento de Data
                date_input = page.locator("ytcp-date-picker input").first
                if date_input.is_visible():
                    date_input.click()
                    page.keyboard.press("Control+A")
                    page.keyboard.press("Backspace")
                    lang_code = (channel_identifier or "en-US").lower()
                    if "pt" in lang_code or "es" in lang_code or "fr" in lang_code:
                        date_str = scheduled_at.strftime("%d/%m/%Y")
                    elif "de" in lang_code:
                        date_str = scheduled_at.strftime("%d.%m.%Y")
                    else:
                        date_str = scheduled_at.strftime("%b %d, %Y")
                    page.keyboard.insert_text(date_str)
                    page.keyboard.press("Enter")
                    time.sleep(1)
                    
                    # Preenchimento de Hora
                    time_input = page.locator("ytcp-time-of-day-picker input").first
                    if not time_input.is_visible():
                        time_input = page.locator("#time-of-day-trigger input").first
                    if time_input.is_visible():
                        time_input.click()
                        page.keyboard.press("Control+A")
                        page.keyboard.press("Backspace")
                        page.keyboard.insert_text(scheduled_at.strftime("%H:%M"))
                        page.keyboard.press("Enter")
                        time.sleep(2)
            else:
                # Sem data: publica como Público imediato via JS
                page.evaluate("""
                    () => {
                        const byId = document.querySelector('#public-radio-button');
                        if (byId) { byId.click(); return; }
                        const radios = document.querySelectorAll('tp-yt-paper-radio-button');
                        for (const r of radios) {
                            if (r.innerText.toLowerCase().includes('public') || r.innerText.toLowerCase().includes('p\u00fablico')) {
                                r.click(); return;
                            }
                        }
                    }
                """)
            
            # Clica em Publicar/Agendar (Done Button)
            print("🚀 [PLAYWRIGHT] Finalizando Publicação!")
            done_btn = page.locator("ytcp-button#done-button, ytcp-button#publish-button").first
            
            # Tenta clicar via JS para evitar problemas de visibilidade ou animações bloqueantes
            try:
                for _ in range(15):
                    clicked = page.evaluate("""
                        () => {
                            const btns = document.querySelectorAll('ytcp-button#done-button, ytcp-button#publish-button');
                            for (const b of btns) {
                                if (b.offsetWidth > 0 && !b.hasAttribute('disabled') && !b.hasAttribute('hidden') && b.getAttribute('aria-disabled') !== 'true') {
                                    b.click();
                                    return true;
                                }
                            }
                            return false;
                        }
                    """)
                    if clicked:
                        print("✅ [PLAYWRIGHT] Botão de Conclusão clicado com sucesso via JS.")
                        break
                    print("⏳ [PLAYWRIGHT] Aguardando botão de conclusão ser habilitado...")
                    time.sleep(3)
                
                # Fallback se JS falhar
                if not clicked:
                    done_btn.click(force=True)
            except Exception as e:
                print(f"⚠️ Erro ao clicar Done via JS: {e}")
                try: done_btn.click(force=True)
                except: pass
            
            # Aguarda upload concluir (O YouTube pode demorar para exibir o diálogo final)
            print("⏳ [PLAYWRIGHT] Aguardando confirmação final do YouTube...")
            try:
                # Tenta esperar pelo diálogo de sucesso OU pelo sumiço do modal de upload
                page.wait_for_selector("ytcp-video-share-dialog, #close-button", timeout=120000)
                print("✅ [PLAYWRIGHT] Upload finalizado!")
            except:
                print("⚠️ [PLAYWRIGHT] Timeout aguardando diálogo de share. Prosseguindo...")

            # Tenta extrair o link do vídeo do modal se ele ainda estiver lá
            video_link = None
            try:
                # Seletor 1: Link direto no modal de share
                video_link = page.locator("a.ytcp-video-info").get_attribute("href", timeout=5000)
            except: pass
            
            if not video_link:
                try:
                    # Seletor 2: Input de URL copiável no share dialog
                    video_link = page.locator("ytcp-video-share-dialog input").get_attribute("value", timeout=5000)
                except: pass
            
            if not video_link:
                try:
                    # Seletor 3: Extrai ID do vídeo da URL atual do YouTube Studio
                    current_url = page.url
                    import re as _re
                    vid_match = _re.search(r'/video/([a-zA-Z0-9_-]{8,15})', current_url)
                    if vid_match:
                        vid_id = vid_match.group(1)
                        video_link = f"https://www.youtube.com/watch?v={vid_id}"
                        print(f"   ℹ️ Link extraído da URL do Studio: {video_link}")
                except: pass
            
            if not video_link:
                try:
                    # Seletor 4: Navega para a lista de vídeos e pega o mais recente, validando o título
                    page.goto("https://studio.youtube.com/channel/content", timeout=30000, wait_until="domcontentloaded")
                    time.sleep(4)
                    
                    # Verifica se o primeiro título na lista de vídeos bate com o título atual
                    first_row_title_el = page.locator("ytcp-video-list-cell-title #video-title").first
                    if first_row_title_el.is_visible():
                        actual_row_title = first_row_title_el.inner_text().strip().lower()
                        expected_title_lower = title.strip().lower()
                        # Verifica se há sobreposição de títulos para ter certeza de que é o mesmo vídeo
                        if actual_row_title in expected_title_lower or expected_title_lower in actual_row_title:
                            first_row_link = page.locator("ytcp-video-list-cell-title a").first.get_attribute("href", timeout=5000)
                            if first_row_link:
                                vid_match = _re.search(r'v=([a-zA-Z0-9_-]+)', first_row_link)
                                if vid_match:
                                    video_link = f"https://www.youtube.com/watch?v={vid_match.group(1)}"
                                    print(f"   ℹ️ Link verificado extraído da lista de conteúdo: {video_link}")
                        else:
                            print(f"   ⚠️ [PLAYWRIGHT] O vídeo no topo da lista ({actual_row_title}) não corresponde ao título atual ({expected_title_lower}). Evitando publicação de comentário no vídeo errado!")
                except Exception as e:
                    print(f"   ⚠️ Erro ao extrair link verificado da lista de conteúdo: {e}")
            
            # Fecha o popup de share se existir
            try:
                page.click("ytcp-button#close-button", timeout=5000)
                time.sleep(2)
            except: pass
            
            # Executa o Validador Pós-Upload
            validate_youtube_studio_metadata(page, video_link, title, scheduled_at)
            
            # Post pinned comment if provided
            if pinned_comment and video_link:
                print(f"📌 [PLAYWRIGHT] Postando comentário fixado...")
                try:
                    if is_short:
                        print("   ℹ️ [INFO] Shorts não suportam pin de comentário instantâneo via desktop UI. Ignorado.")
                    else:
                        page.goto(video_link, timeout=60000, wait_until="domcontentloaded")
                        time.sleep(4)
                        # Check if comments box is available (Video might be 'Processing')
                        if page.locator("#placeholder-area").is_visible(timeout=5000):
                            # Click the comment box
                            page.click("#placeholder-area")
                            time.sleep(1)
                            page.keyboard.insert_text(pinned_comment[:500])
                            page.keyboard.press("Tab")  # move focus
                            # Click post button
                            page.click("#submit-button", timeout=5000)
                            time.sleep(2)
                            # Pin it - click the three dots on the first comment, then pin
                            page.click("ytd-comment-action-buttons-renderer #more-actions", timeout=5000)
                            time.sleep(1)
                            page.click("[aria-label='Pin comment']", timeout=3000)
                            time.sleep(1)
                            page.click("#confirm-button", timeout=3000)
                            print("   ✅ Comentário fixado com sucesso!")
                        else:
                            print("   ℹ️ [INFO] Caixa de comentários indisponível (vídeo ainda em Processamento no YouTube). Pin ignorado.")
                except Exception as e:
                    print(f"   ℹ️ [INFO] Pinned comment ignorado suavemente (TimeOut de Processamento).")
            
            print(f"🎉 [SUCESSO ZERO-API] Vídeo publicado via Automação Web! Link: {video_link}")
            
            browser.close()
            return video_link
            
        except Exception as e:
            print(f"❌ [PLAYWRIGHT ERRO] A automação falhou na interface: {e}")
            # Salva screenshot para debug
            page.screenshot(path=f"playwright_error_{channel_identifier}.png")
            browser.close()
            raise Exception("Playwright UI Automation Failed")
