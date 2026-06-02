import os
import json
import re

def _clean_json(text: str) -> str:
    """Extrai o conteúdo JSON de uma string poluída e tenta consertar erros comuns de LLMs."""
    text = text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    start_dict = text.find('{')
    start_list = text.find('[')
    
    if start_dict == -1 and start_list == -1:
        return text
        
    start = min(start_dict, start_list) if (start_dict != -1 and start_list != -1) else (start_dict if start_dict != -1 else start_list)
    end_dict = text.rfind('}')
    end_list = text.rfind(']')
    end = max(end_dict, end_list)
    
    if start != -1 and end != -1 and end > start:
        return text[start:end+1]
    return text

def heal_truncated_json(json_str: str) -> str:
    json_str = json_str.strip()
    try:
        json.loads(json_str)
        return json_str
    except json.JSONDecodeError:
        pass
    
    positions = [i for i, char in enumerate(json_str) if char == '}']
    for pos in reversed(positions):
        for suffix in ["]}", "}", "]}", "\n]}}", "]\n}"]:
            candidate = json_str[:pos+1] + suffix
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                continue
    return json_str

def clean_cache():
    project_dir = os.path.dirname(os.path.abspath(__file__))
    cache_dir = os.path.join(project_dir, "assets_cache", "llm_cache")
    
    if not os.path.exists(cache_dir):
        print(f"Directory {cache_dir} does not exist.")
        return
        
    files = [f for f in os.listdir(cache_dir) if f.endswith(".json")]
    print(f"Scanning {len(files)} cache files in {cache_dir}...")
    
    deleted_count = 0
    healed_count = 0
    valid_count = 0
    
    for filename in files:
        filepath = os.path.join(cache_dir, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[DELETE] Broken cache file (invalid file JSON) {filename}: {e}")
            try:
                os.remove(filepath)
            except: pass
            deleted_count += 1
            continue
            
        raw_text = data.get("text", "")
        if not raw_text:
            print(f"[DELETE] Empty cache file {filename}")
            try:
                os.remove(filepath)
            except: pass
            deleted_count += 1
            continue
            
        clean_text = _clean_json(raw_text)
        
        # Tenta parsear normalmente
        try:
            parsed = json.loads(clean_text)
            scenes = parsed.get("scenes", parsed.get("Cenas", []))
            if isinstance(scenes, list) and len(scenes) >= 5:
                # Cache 100% válido
                valid_count += 1
                continue
            else:
                print(f"[DELETE] Cache has too few scenes ({len(scenes)}). Deleting to force generation...")
                try:
                    os.remove(filepath)
                except: pass
                deleted_count += 1
                continue
        except Exception:
            pass
            
        # Se falhou, tenta o auto-heal para ver se é reparável
        healed_text = heal_truncated_json(clean_text)
        try:
            parsed_healed = json.loads(healed_text)
            scenes = parsed_healed.get("scenes", parsed_healed.get("Cenas", []))
            if isinstance(scenes, list) and len(scenes) >= 5:
                print(f"[DELETE] Cache was truncated but healable (recovered {len(scenes)} scenes). Deleting to force fresh optimized generation...")
                try:
                    os.remove(filepath)
                except: pass
                deleted_count += 1
                healed_count += 1
                continue
        except Exception:
            pass
            
        # Se falhou no auto-heal, é lixo total
        print(f"[DELETE] Unrecoverable/corrupted cache file {filename}")
        try:
            os.remove(filepath)
        except: pass
        deleted_count += 1

    print("\n--- CACHE SCAN SUMMARY ---")
    print(f"Total Cache Files: {len(files)}")
    print(f"Valid Caches Kept: {valid_count}")
    print(f"Corrupted Caches Deleted: {deleted_count} (including {healed_count} healable but deleted to force fresh run)")
    print("--------------------------")

if __name__ == "__main__":
    clean_cache()
