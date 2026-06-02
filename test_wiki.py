import requests
import urllib.parse
import json

def fetch_wiki_image_improved(entity_name):
    headers = {
        "User-Agent": "DarkChannelFactoryBot/1.0 (contact@canaldarkdjm.com) Python-Requests/2.0"
    }
    
    # 1. Busca Direta com Title Case e redirects
    entity_title = entity_name.title()
    entity_title = entity_title.replace("F1", "F1").replace("Gp", "GP")
    
    url_direct = f"https://en.wikipedia.org/w/api.php?action=query&titles={urllib.parse.quote(entity_title)}&prop=pageimages&format=json&pithumbsize=1280&redirects=1"
    try:
        r = requests.get(url_direct, headers=headers, timeout=5)
        if r.status_code == 200:
            pages = r.json().get("query", {}).get("pages", {})
            for page_id in pages:
                if page_id != "-1":
                    thumb = pages[page_id].get("thumbnail", {}).get("source")
                    if thumb:
                        return f"[DIRETO] {thumb}"
    except Exception as e:
        pass
        
    # 2. Busca de texto (Search API) para achar o artigo mais relevante e obter a pageimage dele
    url_search = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={urllib.parse.quote(entity_name)}&utf8=&format=json"
    try:
        r = requests.get(url_search, headers=headers, timeout=5)
        if r.status_code == 200:
            search_results = r.json().get("query", {}).get("search", [])
            if search_results:
                best_title = search_results[0].get("title")
                url_img = f"https://en.wikipedia.org/w/api.php?action=query&titles={urllib.parse.quote(best_title)}&prop=pageimages&format=json&pithumbsize=1280&redirects=1"
                r2 = requests.get(url_img, headers=headers, timeout=5)
                if r2.status_code == 200:
                    pages = r2.json().get("query", {}).get("pages", {})
                    for page_id in pages:
                        thumb = pages[page_id].get("thumbnail", {}).get("source")
                        if thumb:
                            return f"[BUSCA: '{best_title}'] {thumb}"
    except Exception as e:
        pass
        
    # 3. Fallback Final Super Potente: Busca direta na WIKIMEDIA COMMONS
    # Busca arquivos no namespace 6 (mídia) que correspondam ao termo, ordenados por relevância
    url_commons = f"https://commons.wikimedia.org/w/api.php?action=query&generator=search&gsrsearch={urllib.parse.quote(entity_name)}&gsrnamespace=6&prop=imageinfo&iiprop=url&iiurlwidth=1280&format=json"
    try:
        r = requests.get(url_commons, headers=headers, timeout=5)
        if r.status_code == 200:
            data = r.json()
            pages = data.get("query", {}).get("pages", {})
            for page_id in pages:
                info = pages[page_id].get("imageinfo", [])
                if info:
                    # Preferimos a URL do thumbnail redimensionado (geralmente mais leve e em 1280px) ou a URL direta
                    thumb_url = info[0].get("thumburl")
                    if thumb_url:
                        return f"[COMMONS THUMB] {thumb_url}"
                    direct_url = info[0].get("url")
                    if direct_url:
                        return f"[COMMONS DIRECT] {direct_url}"
    except Exception as e:
        print(f"Erro na busca do Commons: {e}")
        
    return None

terms = [
    "ferrari f1",
    "red bull racing",
    "f1 betting",
    "silverstone",
    "interlagos"
]

for term in terms:
    print(f"\n🏛️ [WIKI] Testando termo: '{term}'...")
    img = fetch_wiki_image_improved(term)
    print(f"  -> Resultado final: {img}")
