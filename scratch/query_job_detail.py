import os
import json
import psycopg2
from dotenv import load_dotenv

load_dotenv()
db_url = os.getenv("DATABASE_URL")
conn = psycopg2.connect(db_url)
cursor = conn.cursor()

cursor.execute("SELECT script_data, error_message, status FROM video_jobs WHERE id='f8827c2d-f91b-4a13-b9af-63607a35fc3c'")
row = cursor.fetchone()
if row:
    script_data_raw, err, status = row
    print("STATUS:", status)
    print("ERROR:", err)
    
    if script_data_raw:
        if isinstance(script_data_raw, dict):
            script_data = script_data_raw
        else:
            script_data = json.loads(script_data_raw)
            
        with open("scratch/script_detail.json", "w", encoding="utf-8") as f:
            json.dump(script_data, f, indent=2, ensure_ascii=False)
        print("Saved script detail to scratch/script_detail.json")
    else:
        print("script_data is empty")
else:
    print("Job not found")

conn.close()
