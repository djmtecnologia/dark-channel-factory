import os
import re
import time
import json
import random
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from playwright.sync_api import sync_playwright
import requests
from dotenv import load_dotenv

# --- CONFIGURACOES ---
load_dotenv()
# O script agora aponta para a pasta real de saída da fábrica
VIDEOS_DIR = Path(os.getcwd()) / "videos_finais"
PROFILES_DIR = Path(os.getenv("CHROME_PROFILES_DIR", r"C:\Users\User\AppData\Local\Google\Chrome\User Data"))

CHANNELS_MAP = {
    "US": {
        "profile": "Profile 11", 
        "region": "US", 
        "niche": "finance_ai", 
        "tone": "optimistic",
        "keywords": ["ROI", "NVIDIA", "401k", "Stock Market"],
        "handle": "@AIBusinessUS"
    },
    "FR": {
        "profile": "Profile 15", 
        "region": "France", 
        "niche": "finance_ai", 
        "tone": "innovative",
        "keywords": ["Économie", "IA Innovation", "Patrimoine"],
        "handle": "@SecretsArgentIA"
    },
    "ES": {
        "profile": "Profile 14", 
        "region": "Spain", 
        "niche": "finance_ai", 
        "tone": "democratic",
        "keywords": ["Ingresos Extra", "Inversión", "Libertad Financiera"],
        "handle": "@NegociosconIA-ES"
    },
    "AL": {
        "profile": "Profile 17", 
        "region": "Germany", 
        "niche": "finance_ai", 
        "tone": "pragmatic",
        "keywords": ["SAP", "ASML", "AI Act", "Efficiency", "Datenschutz"],
        "handle": "@KIBusiness-AL"
    }
}

POST_TIMES = {
    "US": {
        "long": ["11:00", "16:00", "20:00"],
        "shorts": ["09:00", "12:00", "14:00", "15:00", "17:00", "18:00", "21:00", "22:00", "23:00"]
    },
    "France": {
        "long": ["10:00", "15:00", "19:00"],
        "shorts": ["08:00", "11:00", "13:00", "14:00", "16:00", "17:00", "20:00", "21:00", "22:00"]
    },
    "Spain": {
        "long": ["10:00", "15:00", "19:00"],
        "shorts": ["08:00", "11:00", "13:00", "14:00", "16:00", "17:00", "20:00", "21:00", "22:00"]
    },
    "Germany": {
        "long": ["09:00", "14:00", "18:00"],
        "shorts": ["07:00", "10:00", "12:00", "13:00", "15:00", "16:00", "19:00", "20:00", "21:00"]
    }
}

YT_W, YT_H = 1280, 720

class C:
    G = "\033[92m"; Y = "\033[93m"; R = "\033[91m"; CY = "\033[96m"; B = "\033[94m"; DIM = "\033[2m"; RESET = "\033[0m"

def log(tag, msg, color=C.RESET):
    print(f"{color}{tag} {msg}{C.RESET}")

def get_adaptive_schedule(region, video_type, index, base_date=None):
    """Retorna o horario de postagem exato baseado no tipo de video e regiao."""
    if not base_date:
        base_date = datetime.now() + timedelta(days=1) # Producao hoje, postagem amanha
    
    slots = POST_TIMES.get(region, POST_TIMES["US"])[video_type]
    slot_time = slots[index % len(slots)]
    h, m = map(int, slot_time.split(":"))
    return base_date.replace(hour=h, minute=m, second=0, microsecond=0)

# ── 1. SCANNER & METADATA ─────────────────────────────────────────────────────

def scan_today_videos():
    results = []
    log("[SCAN]", "Escaneando videos locais (48h)...", C.DIM)
    
    raw_list = []
    for i in range(2):
        d = datetime.now() - timedelta(days=i)
        date_str = d.strftime("%d-%m-%Y")
        for ch_code, ch_info in CHANNELS_MAP.items():
            # Tenta as duas ordens de pasta possíveis
            dirs_to_check = [
                VIDEOS_DIR / ch_code / date_str,
                VIDEOS_DIR / date_str / ch_code
            ]
            for ch_dir in dirs_to_check:
                if not ch_dir.exists(): continue
                for mf in sorted(ch_dir.glob("metadata_*.txt")):
                    jid = mf.stem.replace("metadata_", "")
                    raw_list.append({
                        "jid": jid, "mf": mf, "ch_code": ch_code, "ch_info": ch_info, "date_str": date_str, "ch_dir": ch_dir
                    })

    needing_meta = []
    for v in raw_list:
        meta = _parse_meta(v["mf"])
        t = meta.get("title", "")
        if "Explorando novo" in t or "ângulo viral" in t or len(t.split()) < 3:
            needing_meta.append(v)
    
    batch_results = []
    if needing_meta:
        batch_results = _generate_batch_metadata(len(needing_meta), os.getenv("PRIMARY_NICHE", "ai_tools"))

    batch_idx = 0
    for v in raw_list:
        meta = _parse_meta(v["mf"])
        title, desc, tags = meta.get("title", "AI Video"), meta.get("description", ""), meta.get("tags", "")

        if v in needing_meta and batch_idx < len(batch_results):
            title = batch_results[batch_idx].get("title", title)
            desc = batch_results[batch_idx].get("description", desc)
            tags = batch_results[batch_idx].get("tags", tags)
            batch_idx += 1
        
        video_path = v["ch_dir"] / f"video_longo_{v['jid']}.mp4"
        thumb_path = v["ch_dir"] / f"thumb_{v['jid']}.jpg"

        results.append({
            "job_id": v["jid"], "date": v["date_str"], "channel": v["ch_code"],
            "profile": v["ch_info"]["profile"], "region": v["ch_info"]["region"],
            "video": str(video_path) if video_path.exists() else None,
            "thumb": str(thumb_path), "has_thumb": thumb_path.exists(),
            "title": title, "desc": desc, "tags": tags,
        })
    return results

def _parse_meta(path):
    data, key, buf = {}, None, []
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("TITLE:"): data["title"] = line.replace("TITLE:", "").strip()
            elif line.startswith("DESCRIPTION:"): key = "description"; buf = []
            elif line.startswith("TAGS:"):
                if key == "description": data["description"] = "\n".join(buf).strip()
                data["tags"] = line.replace("TAGS:", "").strip()
                key = None; buf = []
            elif key: buf.append(line)
        if key and buf: data[key] = "\n".join(buf).strip()
    except Exception: pass
    return data

def _generate_batch_metadata(count, niche):
    log("[LLM]", f"Iniciando geracao em lote para {count} videos...", C.CY)
    prompt = f"Generate viral YouTube metadata for {count} DIFFERENT videos about {niche}. Return ONLY a JSON list of objects: [{{'title': '...', 'description': '...', 'tags': '...'}}]. No hashes/IDs in titles. Use emojis."
    
    keys = {"MISTRAL": os.getenv("MISTRAL_API_KEY"), "DEEPSEEK": os.getenv("DEEPSEEK_API_KEY"), "OPENROUTER": os.getenv("OPENROUTER_API_KEY")}
    
    for prov, key in keys.items():
        if not key: continue
        try:
            url = "https://api.mistral.ai/v1/chat/completions" if prov=="MISTRAL" else "https://api.deepseek.com/v1/chat/completions"
            if prov=="OPENROUTER": url = "https://openrouter.ai/api/v1/chat/completions"
            
            headers = {"Authorization": f"Bearer {key}"}
            model = "mistral-large-latest" if prov=="MISTRAL" else "deepseek-chat"
            if prov=="OPENROUTER": model = "google/gemini-2.0-flash-001"
            
            r = requests.post(url, headers=headers, json={"model": model, "messages": [{"role": "user", "content": prompt}]}, timeout=45)
            if r.status_code == 200:
                txt = r.json()['choices'][0]['message']['content']
                if "```" in txt: txt = txt.split("```")[1].replace("json","").strip()
                data = json.loads(txt.strip())
                if isinstance(data, dict):
                    for k in data:
                        if isinstance(data[k], list): data = data[k]; break
                if isinstance(data, list): log("[OK]", f"Lote via {prov}!", C.G); return data
        except Exception: continue
    return []

# ── 2. THUMBNAIL ──────────────────────────────────────────────────────────────

def _resize(img_data):
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(img_data))
        img = img.resize((YT_W, YT_H), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=90)
        return out.getvalue()
    except Exception: return img_data

def generate_thumb(title, out):
    clean_ctx = re.sub(r'[^\w\s]', '', title).strip()
    # Prompt otimizado para o mercado US (High CTR)
    p = (f"Ultra-realistic cinematic YouTube thumbnail, {clean_ctx}, expressive face showing shock, "
         "vibrant high-contrast colors, depth of field, 8k resolution, professional digital art style, "
         "mysterious and intriguing atmosphere, no text.")
    url = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(p[:800])}?width={YT_W}&height={YT_H}&nologo=true&enhance=true&seed={int(time.time())%99999}"
    try:
        r = requests.get(url, timeout=60)
        if r.status_code == 200 and len(r.content) > 5000:
            with open(out, "wb") as f: f.write(_resize(r.content))
            return True
    except Exception: pass
    return False

# ── 3. PLAYWRIGHT / STUDIO ────────────────────────────────────────────────────

def _launch_browser(profile):
    pw = sync_playwright().start()
    pp = str(PROFILES_DIR / profile)
    log("[BROWSER]", f"Iniciando perfil: {profile}", C.DIM)
    
    # Se o Chrome estiver aberto, o Playwright vai falhar. 
    # Tentamos usar argumentos que minimizam conflitos.
    try:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=pp, 
            headless=False, 
            channel="chrome",
            no_viewport=True,
            ignore_default_args=["--enable-automation"],
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--start-maximized"
            ]
        )
        return pw, ctx, ctx.new_page()
    except Exception as e:
        log("[ERR]", f"Erro ao abrir browser. Certifique-se de que o Chrome esta FECHADO!\n{e}", C.R)
        pw.stop()
        raise e

def _collect_yt_ids(page, ch_id, target=30):
    url = f"https://studio.youtube.com/channel/{ch_id}/videos/upload2"
    log("[NAV]", f"Abrindo Studio: {url}", C.DIM)
    
    # Tenta abrir e garante que esta logado
    page.goto(url, timeout=60000)
    time.sleep(5)
    
    if "login" in page.url or "signin" in page.url:
        log("[WARN]", "Sessao expirada ou perfil incorreto. Por favor, faca login manualmente na janela que abriu.", C.Y)
        page.wait_for_url("**/studio.youtube.com/**", timeout=300000) # Espera ate 5 min o user logar
    try:
        if page.locator("#clear-filters-button").is_visible(): page.locator("#clear-filters-button").click(); time.sleep(2)
    except Exception: pass
    log("[SCROLL]", "Buscando rascunhos...", C.DIM)
    for _ in range(15):
        page.keyboard.press("PageDown")
        time.sleep(1)
        if page.locator("ytcp-video-row").count() >= target: break
    html = page.content()
    ids = list(set(re.findall(r'video/([a-zA-Z0-9_-]{11})', html)))
    log("[OK]", f"{len(ids)} IDs encontrados.", C.G)
    return ids[:target]

def _build_desc(title, original_desc=""):
    if original_desc and "http" in original_desc:
        return original_desc
        
    ds_id = os.getenv("DIGISTORE_AFFILIATE_ID", "AIBusinessDK")
    link = f"https://www.digistore24.com/redir/834190/{ds_id}"
    return f"{title}\n\nShocking AI secrets revealed. Master automation today.\n\n🚀 START HERE: {link}\n\n#AI #Automation #Success"

def _edit_video(page, ytid, video_info, scheduled_at):
    log("[WIZARD]", f"Processando YT:{ytid}", C.B)
    url = f"https://studio.youtube.com/video/{ytid}/edit?on_draft=true"
    page.goto(url, timeout=60000)
    time.sleep(6)
    
    title, thumb = video_info["title"], video_info["thumb"]
    desc = _build_desc(title, video_info.get("desc", ""))

    try:
        # Passo 1: Detalhes
        log("[STEP 1]", "Preenchendo Detalhes...", C.DIM)
        t_box = page.locator("#textbox[aria-label*='título'], #title-textarea #textbox").first
        t_box.click(); page.keyboard.press("Control+A"); page.keyboard.press("Backspace"); page.keyboard.type(title[:100])
        
        d_box = page.locator("#description-textarea #textbox").first
        d_box.click(); page.keyboard.press("Control+A"); page.keyboard.press("Backspace"); page.keyboard.type(desc[:4900])

        if os.path.exists(thumb):
            # BUG FIX: Use the specific thumbnail input selector, not the generic file input
            # The generic input would overwrite the video upload with the image
            try:
                thumb_input = page.locator("#thumbnails input[type='file'], ytcp-thumbnail-uploader input[type='file']").first
                thumb_input.set_input_files(thumb)
                time.sleep(4)
            except Exception as te:
                print(f"\u26a0\ufe0f Thumb upload failed: {te}")

        # Avanca 3 telas
        for _ in range(3):
            btn = page.locator("#next-button")
            btn.wait_for(state="visible"); btn.click(); time.sleep(2)

        # Passo 4: Visibilidade / Agendar
        log("[STEP 4]", f"Agendando: {scheduled_at.strftime('%d/%m/%Y %H:%M')}", C.DIM)
        page.locator("#schedule-radio-button, [name='SCHEDULE']").click(); time.sleep(1)
        
        # Data
        page.locator("#datepicker-trigger").click(); time.sleep(1)
        page.keyboard.press("Control+A"); page.keyboard.type(scheduled_at.strftime("%d/%m/%Y")); page.keyboard.press("Enter")
        time.sleep(1)
        
        # Hora
        page.locator("input[aria-label*='horário'], input[aria-label*='Time']").click(); time.sleep(1)
        page.keyboard.press("Control+A"); page.keyboard.type(scheduled_at.strftime("%H:%M")); page.keyboard.press("Enter")
        time.sleep(1)

        final = page.locator("#done-button")
        if final.is_visible():
            final.click()
            log("[OK]", "Video Programado!", C.G)
            time.sleep(5); return True
    except Exception as e:
        log("[FAIL]", f"Erro no Wizard: {e}", C.R)
    return False

# ── 4. ORCHESTRATOR ───────────────────────────────────────────────────────────

def get_schedule_times(region, n):
    # BUG FIX: 'North America' doesn't exist in POST_TIMES dict - was causing KeyError
    slots_key = region if region in POST_TIMES else "US"
    slots = POST_TIMES.get(slots_key, POST_TIMES["US"])
    # Flatten long + shorts into one list for scheduling
    all_slots = slots.get("long", []) + slots.get("shorts", [])
    res, day, si = [], 1, 0
    for _ in range(n):
        h, m = map(int, all_slots[si % len(all_slots)].split(":"))
        res.append(datetime.now().replace(hour=h, minute=m, second=0, microsecond=0) + timedelta(days=day))
        si += 1
        if si % len(all_slots) == 0: day += 1
    return res

def main():
    print(f"\n{C.B}================================================\n FIX DRAFTS & PUBLISH - Dark Channel Factory\n================================================{C.RESET}")
    jobs = scan_today_videos()
    if not jobs: log("[INFO]", "Nada para processar."); return

    print(f"\n{'#':<4} {'Canal':<6} {'Data':<12} {'Thumb':<8} {'Titulo'}")
    print("-" * 80)
    for i, j in enumerate(jobs, 1):
        st = "OK" if j["has_thumb"] else "--"
        print(f"[{i:<2}] {j['channel']:<6} {j['date']:<12} {st:<8} {j['title'][:50]}...")

    sel = input("\nEscolha (todos/numeros/sair): ").strip().lower()
    if sel == 'sair': return
    
    to_process = jobs
    if sel != 'todos':
        idxs = [int(x.strip())-1 for x in sel.split(",")]
        to_process = [jobs[i] for i in idxs if 0 <= i < len(jobs)]

    # Agrupa por canal
    by_chan = {}
    for j in to_process:
        by_chan.setdefault(j["channel"], []).append(j)

    for ch, vids in by_chan.items():
        log("[CANAL]", f"{ch} - {len(vids)} video(s)", C.B)
        try:
            pw, ctx, page = _launch_browser(CHANNELS_MAP[ch]["profile"])
        except Exception: continue

        try:
            # 1. PEGAR ID DO CANAL COM PACIENCIA (ATE 1 HORA PARA LOGIN)
            log("[INFO]", "JANELA ABERTA. Por favor, faca login se necessario.", C.Y)
            log("[INFO]", "O robo esta aguardando voce entrar no YouTube Studio...", C.DIM)
            
            # Navega apenas uma vez no inicio
            try: page.goto("https://studio.youtube.com", timeout=60000)
            except: pass
            
            ch_id = ""
            start_wait = time.time()
            while (time.time() - start_wait) < 3600:
                # Verificacao passiva: apenas olha a URL e o codigo, sem navegar!
                curr_url = page.url
                m = re.search(r'/channel/([^/?#]+)', curr_url)
                if m: ch_id = m.group(1); break
                
                # Tenta buscar no codigo fonte (apenas se ja estiver no dominio do youtube)
                if "youtube.com" in curr_url:
                    try:
                        html = page.content()
                        m2 = re.search(r'\"channelId\":\"([a-zA-Z0-9_-]{20,})\"', html)
                        if m2: ch_id = m2.group(1); break
                    except Exception: pass

                time.sleep(3) # Verifica a cada 3 segundos em silencio total
            
            if not ch_id:
                log("[ERR]", "Tempo esgotado sem detectar o canal.", C.R)
                continue
            
            log("[OK]", f"Canal Detectado: {ch_id}. Iniciando agendamento...", C.G)
            yt_ids = _collect_yt_ids(page, ch_id, len(vids))
            schedules = get_schedule_times(CHANNELS_MAP[ch]["region"], len(vids))
            
            for idx, j in enumerate(vids):
                if idx >= len(yt_ids): break
                if not j["has_thumb"]: generate_thumb(j["title"], j["thumb"])
                _edit_video(page, yt_ids[idx], j, schedules[idx])
        finally:
            ctx.close(); pw.stop()

if __name__ == "__main__":
    main()
