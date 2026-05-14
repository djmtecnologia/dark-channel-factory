"""
🎨 REGENERADOR DE THUMBNAILS — Dark Channel Factory
==================================================
Uso: python regenerar_thumbs.py  (ou clique em REGENERAR_THUMBS.bat)

COMO FUNCIONA:
  1. Lista os vídeos gerados nas últimas 48h
  2. Você escolhe qual(is) quer nova thumb
  3. Descreve livremente o contexto (ex: "pessoa chocada vendo IA roubar dinheiro, fundo vermelho escuro")
  4. Escolhe quantas variações quer (1-5)
  5. Escolhe a melhor variação — salva em 1280x720 padrão YouTube
"""

import os
import io
import time
import shutil
import urllib.parse
import requests
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────
VIDEOS_DIR   = Path(__file__).parent / "videos_finais"
CHANNELS     = {"US": "en-US", "FR": "fr-FR", "ES": "es-ES", "DE": "de-DE"}
YT_W, YT_H   = 1280, 720   # Padrão oficial YouTube 16:9

# ─────────────────────────────────────────────────────────────────────────────
# CORES ANSI
# ─────────────────────────────────────────────────────────────────────────────
class C:
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    CYAN   = "\033[96m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RESET  = "\033[0m"

def banner():
    print(f"""
{C.CYAN}{C.BOLD}
  ╔══════════════════════════════════════════════════════╗
  ║   🎨  REGENERADOR DE THUMBNAILS — Dark Factory      ║
  ║   Tamanho padrão YouTube: {YT_W}×{YT_H} (16:9)         ║
  ╚══════════════════════════════════════════════════════╝
{C.RESET}""")

# ─────────────────────────────────────────────────────────────────────────────
# Utilitário: garante que qualquer imagem fique em exatamente 1280×720
# ─────────────────────────────────────────────────────────────────────────────
def _resize_to_yt(img_bytes: bytes) -> bytes:
    """Redimensiona bytes de imagem para 1280×720 mantendo qualidade máxima."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img = img.resize((YT_W, YT_H), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        return buf.getvalue()
    except ImportError:
        # Pillow não instalado — retorna os bytes originais com aviso
        print(f"  {C.YELLOW}⚠ Pillow não encontrado. Execute: pip install Pillow{C.RESET}")
        print(f"  {C.DIM}  Imagem salva sem redimensionamento.{C.RESET}")
        return img_bytes

def _save_resized(img_bytes: bytes, output_path: str) -> bool:
    """Salva imagem garantindo 1280×720."""
    final_bytes = _resize_to_yt(img_bytes)
    with open(output_path, "wb") as f:
        f.write(final_bytes)
    return True

# ─────────────────────────────────────────────────────────────────────────────
# Varredura de vídeos (últimas 48h)
# ─────────────────────────────────────────────────────────────────────────────
def scan_videos():
    videos = []
    today     = datetime.now().strftime("%d-%m-%Y")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%d-%m-%Y")

    for date_str in [today, yesterday]:
        date_dir = VIDEOS_DIR / date_str
        if not date_dir.exists():
            continue
        for ch_code, ch_lang in CHANNELS.items():
            ch_dir = date_dir / ch_code
            if not ch_dir.exists():
                continue
            for meta_file in ch_dir.glob("metadata_*.txt"):
                job_id     = meta_file.stem.replace("metadata_", "")
                video_file = ch_dir / f"video_longo_{job_id}.mp4"
                thumb_file = ch_dir / f"thumb_{job_id}.jpg"

                title = "Sem título"
                try:
                    for line in meta_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                        if line.startswith("TITLE:"):
                            title = line.replace("TITLE:", "").strip()
                            break
                except Exception:
                    pass

                videos.append({
                    "index":      len(videos) + 1,
                    "job_id":     job_id,
                    "title":      title,
                    "channel":    ch_code,
                    "language":   ch_lang,
                    "date":       date_str,
                    "video_path": str(video_file) if video_file.exists() else None,
                    "thumb_path": str(thumb_file),
                    "has_thumb":  thumb_file.exists(),
                })
    return videos

# ─────────────────────────────────────────────────────────────────────────────
# Geradores de Imagem (todos salvam em 1280×720 via _save_resized)
# ─────────────────────────────────────────────────────────────────────────────
def _gen_dalle(context: str, output_path: str) -> bool:
    """DALL-E 3 — melhor qualidade. Gera em 1792×1024 e redimensiona para 1280×720."""
    openai_key = os.getenv("OPENAI_API_KEY", "")
    if not openai_key:
        return False
    try:
        from openai import OpenAI
        client = OpenAI(api_key=openai_key)
        prompt = (
            f"Professional YouTube thumbnail, exactly 16:9 aspect ratio. "
            f"Ultra high detail, dramatic cinematic lighting, no blur, no distortion. "
            f"Context: {context}. "
            f"Style requirements: high contrast colors (deep red, bright yellow, electric blue or neon orange), "
            f"bold readable text with thick black stroke, shocking facial expression, viral dark documentary look. "
            f"The image must fill the entire frame — no letterboxing, no black bars."
        )
        response = client.images.generate(
            model="dall-e-3",
            prompt=prompt[:4000],
            size="1792x1024",   # 16:9 mais próximo disponível no DALL-E
            quality="hd",
            n=1,
        )
        img_bytes = requests.get(response.data[0].url, timeout=30).content
        return _save_resized(img_bytes, output_path)
    except Exception as e:
        print(f"  {C.YELLOW}⚠ DALL-E falhou: {e}{C.RESET}")
    return False


def _gen_gemini(context: str, output_path: str) -> bool:
    """Gemini Imagen 3 — 16:9 nativo."""
    gemini_key = os.getenv("GEMINI_API_KEY", "")
    if not gemini_key:
        return False
    try:
        import base64
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"imagen-3.0-generate-002:predict?key={gemini_key}"
        )
        payload = {
            "instances": [{"prompt": (
                f"YouTube thumbnail 16:9. Ultra dramatic. Context: {context}. "
                f"Style: viral dark channel, neon glow, high contrast, bold readable text, no blur."
            )}],
            "parameters": {"sampleCount": 1, "aspectRatio": "16:9"}
        }
        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code == 200:
            predictions = resp.json().get("predictions", [])
            if predictions and "bytesBase64Encoded" in predictions[0]:
                img_bytes = base64.b64decode(predictions[0]["bytesBase64Encoded"])
                return _save_resized(img_bytes, output_path)
    except Exception as e:
        print(f"  {C.YELLOW}⚠ Gemini Imagen falhou: {e}{C.RESET}")
    return False


def _gen_pollinations(context: str, output_path: str, seed: int = 0) -> bool:
    """Pollinations.ai com enhance=true — gratuito e ilimitado."""
    prompt = (
        f"Ultra high quality YouTube thumbnail, 16:9, no black bars, full frame. "
        f"Dramatic cinematic dark background, vivid colors (red, yellow, neon orange), "
        f"bold white text with thick black outline (sharp, no blur). "
        f"Style: MrBeast + Hollywood blockbuster + viral dark documentary. "
        f"Context: {context}"
    )
    safe = urllib.parse.quote(prompt[:800])
    url  = (
        f"https://image.pollinations.ai/prompt/{safe}"
        f"?width={YT_W}&height={YT_H}&nologo=true&enhance=true&seed={seed}"
    )
    try:
        resp = requests.get(url, timeout=60)
        if resp.status_code == 200 and len(resp.content) > 5000:
            # Pollinations já retorna na resolução pedida, mas garantimos o resize
            return _save_resized(resp.content, output_path)
        else:
            print(f"  {C.YELLOW}⚠ Pollinations: status {resp.status_code} ({len(resp.content)} bytes){C.RESET}")
    except Exception as e:
        print(f"  {C.RED}✗ Erro Pollinations: {e}{C.RESET}")
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Orquestrador: DALL-E → Gemini → Pollinations
# ─────────────────────────────────────────────────────────────────────────────
def generate_to_path(output_path: str, context: str, seed: int = 0) -> bool:
    providers = [
        ("🎨 DALL-E 3 (OpenAI HD)",    lambda: _gen_dalle(context, output_path)),
        ("🔮 Gemini Imagen 3",          lambda: _gen_gemini(context, output_path)),
        ("🌐 Pollinations (Enhance)",   lambda: _gen_pollinations(context, output_path, seed=seed)),
    ]
    for name, fn in providers:
        print(f"  ⏳ Tentando {name}...")
        try:
            if fn():
                sz = os.path.getsize(output_path) // 1024
                print(f"  {C.GREEN}✅ {name} — {YT_W}×{YT_H} — {sz} KB → {os.path.basename(output_path)}{C.RESET}")
                return True
        except Exception as e:
            print(f"  {C.RED}✗ {name} erro: {e}{C.RESET}")
        time.sleep(2)
    print(f"  {C.RED}❌ Todos os provedores falharam.{C.RESET}")
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Sugestão automática de contexto com base no título
# ─────────────────────────────────────────────────────────────────────────────
def _suggest_context(title: str) -> str:
    t = title.lower()
    hook = " ".join(title.replace("...", "").replace("?", "").split()[:5]).upper()

    if "ai" in t or "artificial" in t:
        visual = "futuristic AI robot stealing cash, glowing red circuit lines, dark dramatic background"
    elif "money" in t or "rich" in t or "income" in t:
        visual = "person holding stacks of cash in shock, golden coins exploding, cinematic dark background"
    elif "secret" in t or "hidden" in t:
        visual = "mysterious shadowy figure, red classified stamp, spotlight on secret document"
    elif "job" in t or "work" in t or "career" in t:
        visual = "person looking devastated at laptop showing layoff notice, dark office, dramatic red lighting"
    else:
        visual = "person shocked with hands on face, dramatic dark background, glowing neon effects"

    return f'Bold text "{hook}" — {visual}'


# ─────────────────────────────────────────────────────────────────────────────
# Interface Interativa Principal
# ─────────────────────────────────────────────────────────────────────────────
def main():
    banner()
    os.system("")  # Ativa cores ANSI no Windows

    print(f"{C.BOLD}🔍 Escaneando vídeos das últimas 48 horas...{C.RESET}\n")
    videos = scan_videos()

    if not videos:
        print(f"{C.RED}❌ Nenhum vídeo encontrado nos últimos 2 dias.{C.RESET}")
        print("Execute o RUN_FACTORY.bat primeiro para gerar vídeos.")
        input("\nPressione ENTER para sair...")
        return

    # ── Lista de vídeos ──────────────────────────────────────────────────────
    print(f"{'#':<4} {'Canal':<6} {'Data':<12} {'Thumb':<14} Título")
    print("─" * 80)
    for v in videos:
        status     = f"{C.GREEN}✅ OK{C.RESET}" if v["has_thumb"] else f"{C.RED}❌ AUSENTE{C.RESET}"
        title_disp = v["title"][:45] + "..." if len(v["title"]) > 45 else v["title"]
        print(f"[{v['index']:<2}] {v['channel']:<6} {v['date']:<12} {status:<22} {title_disp}")

    print(f"\n{C.BOLD}Comandos:{C.RESET}")
    print(f"  • Número único  (ex: {C.CYAN}3{C.RESET})        → regenerar a thumb daquele vídeo")
    print(f"  • Vários        (ex: {C.CYAN}1,3,5{C.RESET})    → regenerar várias de uma vez")
    print(f"  • {C.CYAN}todos{C.RESET}                        → regenerar TODAS as thumbs do dia")
    print(f"  • {C.CYAN}sair{C.RESET}                         → encerrar")
    print()

    while True:
        choice = input(f"{C.BOLD}👉 Sua escolha: {C.RESET}").strip().lower()

        if choice in ("sair", "exit", "q"):
            print(f"\n{C.GREEN}👋 Até mais!{C.RESET}")
            break

        selected = []
        if choice == "todos":
            selected = videos
        else:
            try:
                indices  = [int(x.strip()) for x in choice.split(",")]
                selected = [v for v in videos if v["index"] in indices]
                if not selected:
                    print(f"{C.RED}  ⚠ Nenhum vídeo com esses números.{C.RESET}")
                    continue
            except ValueError:
                print(f"{C.RED}  ⚠ Entrada inválida.{C.RESET}")
                continue

        # ── Processa cada vídeo selecionado ─────────────────────────────────
        for video in selected:
            final_path = video["thumb_path"]

            print(f"\n{'═'*60}")
            print(f"  {C.BOLD}🎬 [{video['channel']}] {video['title']}{C.RESET}")
            print(f"  {C.DIM}📂 Destino: {final_path}{C.RESET}")
            print(f"{'═'*60}")

            # ── CONTEXTO LIVRE ───────────────────────────────────────────────
            sugestao = _suggest_context(video["title"])
            print(f"\n  {C.YELLOW}{C.BOLD}📋 CONTEXTO DA THUMBNAIL:{C.RESET}")
            print(f"  Descreva livremente o que você quer na imagem.")
            print(f"  Você pode informar: texto, cores, personagens, objetos, emoção, estilo...")
            print(f"\n  {C.DIM}Exemplos:{C.RESET}")
            print(f"  {C.DIM}• 'pessoa chocada segurando nota de dólar, fundo vermelho escuro, texto: AI ROUBOU TUDO'{C.RESET}")
            print(f"  {C.DIM}• 'robô de IA destruindo empregos, homem olhando para câmera assustado, cores neon'{C.RESET}")
            print(f"  {C.DIM}• 'pilha de dinheiro pegando fogo, texto: SEGREDO REVELADO, estilo documentário'{C.RESET}")
            print(f"\n  {C.CYAN}Sugestão automática:{C.RESET}")
            print(f"  {C.DIM}{sugestao}{C.RESET}\n")

            raw_input = input(f"  {C.BOLD}✏️  Seu contexto (ou ENTER para usar a sugestão): {C.RESET}").strip()
            context   = raw_input if raw_input else sugestao

            print(f"\n  {C.GREEN}✔ Contexto confirmado:{C.RESET} {context[:100]}{'...' if len(context) > 100 else ''}")

            # ── Número de variações ──────────────────────────────────────────
            print(f"\n  {C.YELLOW}Quantas variações gerar? (1-5, padrão = 3):{C.RESET}")
            n_str      = input("  Quantidade: ").strip()
            n_variants = max(1, min(5, int(n_str) if n_str.isdigit() else 3))

            # ── Backup único da thumb atual ──────────────────────────────────
            if os.path.exists(final_path):
                bk = final_path.replace(".jpg", f"_backup_{int(time.time())}.jpg")
                shutil.copy(final_path, bk)
                print(f"\n  {C.YELLOW}📦 Backup salvo: {os.path.basename(bk)}{C.RESET}")

            # ── Gera N variações em arquivos temporários ─────────────────────
            generated = []  # [(path, size_bytes)]
            for i in range(n_variants):
                print(f"\n  {C.BOLD}┌─ Variação {i+1}/{n_variants} ─────────────────────────────┐{C.RESET}")
                vpath = final_path.replace(".jpg", f"_v{i+1}.jpg")
                seed  = (int(time.time()) + i * 1000) % 99999
                ok    = generate_to_path(vpath, context, seed=seed)
                size  = os.path.getsize(vpath) if ok and os.path.exists(vpath) else 0
                generated.append((vpath, size))
                if i < n_variants - 1:
                    print(f"  {C.DIM}  ⏳ Aguardando 4s para próxima variação...{C.RESET}")
                    time.sleep(4)

            # ── Seleciona a variação final ───────────────────────────────────
            successful = [(p, s) for p, s in generated if s > 0]
            if not successful:
                print(f"\n  {C.RED}❌ Nenhuma variação gerada.{C.RESET}")
                continue

            chosen_path = None

            if n_variants == 1:
                chosen_path = successful[0][0]
                print(f"\n  {C.GREEN}✅ Variação única — aplicando automaticamente...{C.RESET}")
            else:
                best_path, best_size = max(successful, key=lambda x: x[1])
                print(f"\n  {C.BOLD}Escolha a variação final:{C.RESET}")
                for i, (vpath, vsize) in enumerate(generated):
                    if vsize > 0:
                        marker = f" {C.GREEN}⭐ (recomendada){C.RESET}" if vpath == best_path else ""
                        print(f"  [{i+1}] Variação {i+1} — {vsize//1024} KB{marker}")
                print(f"  [A] Automático — usa a recomendada ⭐")

                var_choice = input("\n  Escolha (número ou A): ").strip().upper()
                if var_choice == "A" or not var_choice.isdigit():
                    chosen_path = best_path
                else:
                    idx = int(var_choice) - 1
                    if 0 <= idx < len(generated) and generated[idx][1] > 0:
                        chosen_path = generated[idx][0]
                    else:
                        print(f"  {C.YELLOW}⚠ Escolha inválida — usando a recomendada.{C.RESET}")
                        chosen_path = best_path

            # ── Copia para o destino final ────────────────────────────────────
            if chosen_path and os.path.exists(chosen_path):
                shutil.copy(chosen_path, final_path)
                sz = os.path.getsize(final_path) // 1024
                print(f"\n  {C.GREEN}{'═'*56}")
                print(f"  ✅ Thumb final salva! {YT_W}×{YT_H} | {sz} KB")
                print(f"  📂 {final_path}")
                print(f"  {'═'*56}{C.RESET}")
            else:
                print(f"\n  {C.RED}❌ Falha ao copiar para o destino.{C.RESET}")

            # ── Remove temporários ────────────────────────────────────────────
            for i in range(n_variants):
                vpath = final_path.replace(".jpg", f"_v{i+1}.jpg")
                if os.path.exists(vpath):
                    os.remove(vpath)

        # ── Pergunta se quer continuar ────────────────────────────────────────
        print(f"\n{C.GREEN}{'─'*60}")
        print(f"✅ Lote concluído! Thumbs salvas em: videos_finais/")
        print(f"{'─'*60}{C.RESET}\n")

        again = input(f"{C.BOLD}Regenerar mais alguma thumb? (s/N): {C.RESET}").strip().lower()
        if again not in ("s", "sim", "y", "yes"):
            print(f"\n{C.GREEN}👋 Encerrando. Thumbs prontas!{C.RESET}")
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n{C.YELLOW}⚠ Interrompido pelo usuário.{C.RESET}")
    input("\nPressione ENTER para fechar...")
