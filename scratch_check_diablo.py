import os
import json
import psycopg2
from dotenv import load_dotenv

load_dotenv()
db_url = os.getenv("DATABASE_URL")
conn = psycopg2.connect(db_url)
cursor = conn.cursor()

# 1. Obter informações básicas
cursor.execute("SELECT id, topic, status, video_type, target_language, error_message, created_at, script_data FROM video_jobs WHERE id='e8fa1134-d3de-49e1-a571-4e681f41897d'")
row = cursor.fetchone()

if row:
    jid, topic, status, vtype, lang, err, created_at, script_data_raw = row
    print("=== JOB DETALHADO ===")
    print("ID:", jid)
    print("TÓPICO:", topic)
    print("STATUS:", status)
    print("TIPO:", vtype)
    print("LANG:", lang)
    print("ERRO:", err)
    print("CRIADO EM:", created_at)
    
    if script_data_raw:
        if isinstance(script_data_raw, dict):
            script_data = script_data_raw
        else:
            script_data = json.loads(script_data_raw)
            
        print("\n=== ESTRUTURA SCRIPT_DATA ===")
        print("Keys:", list(script_data.keys()))
        
        # Salva o script_data para análise posterior
        with open("scratch/diablo_script_detail.json", "w", encoding="utf-8") as f:
            json.dump(script_data, f, indent=2, ensure_ascii=False)
        print("Script_data salvo em scratch/diablo_script_detail.json")
        
        # Mostrar informações das cenas e mídias
        if 'scenes' in script_data:
            scenes = script_data['scenes']
            print(f"Total de cenas: {len(scenes)}")
            for idx, scene in enumerate(scenes):
                print(f"\nCena {idx + 1}:")
                print(f"  Texto: {scene.get('text', '')[:100]}...")
                print(f"  Tipo de mídia: {scene.get('media_type', '')}")
                print(f"  URL/Prompt de mídia: {scene.get('media_url', '') or scene.get('media_prompt', '')}")
                print(f"  Subtipo: {scene.get('media_sub_type', '')}")
                print(f"  Asset local: {scene.get('local_asset_path', '')}")
        else:
            print("Não há 'scenes' no script_data")
    else:
        print("script_data está vazio!")
else:
    print("Job Diablo 4 não encontrado!")

# 2. Buscar outros jobs recentes com status parecidos ou tópicos Diablo
print("\n=== OUTROS JOBS RECENTES DIABLO OU PENDING ===")
cursor.execute("SELECT id, topic, status, target_language, created_at FROM video_jobs WHERE topic ILIKE '%Diablo%' OR status = 'PENDING' ORDER BY created_at DESC LIMIT 10")
rows = cursor.fetchall()
for r in rows:
    print(f"ID: {r[0]} | Topic: {r[1]} | Status: {r[2]} | Lang: {r[3]} | Created: {r[4]}")

conn.close()
