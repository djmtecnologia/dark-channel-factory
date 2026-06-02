import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()
db_url = os.getenv("DATABASE_URL")
conn = psycopg2.connect(db_url)
cursor = conn.cursor()

with open("scratch/db_dump.txt", "w", encoding="utf-8") as f:
    def log(msg):
        f.write(str(msg) + "\n")

    # Ver todos os video_jobs recentes
    cursor.execute("""
        SELECT id, status, topic, video_type, published_url, error_message, created_at 
        FROM video_jobs 
        ORDER BY created_at DESC 
        LIMIT 20
    """)
    jobs = cursor.fetchall()
    log("=== RECENT JOBS ===")
    for j in jobs:
        log(f"ID: {j[0]} | Status: {j[1]} | Type: {j[3]} | Topic: {j[2]}\n  URL: {j[4]}\n  Error: {j[5]}\n  Created At: {j[6]}\n")

    # Ver todos os videos recentes
    cursor.execute("""
        SELECT id, youtube_video_id, title, language, created_at 
        FROM videos 
        ORDER BY created_at DESC 
        LIMIT 20
    """)
    videos = cursor.fetchall()
    log("=== RECENT VIDEOS IN VIDEOS TABLE ===")
    for v in videos:
        log(f"ID: {v[0]} | YT ID: {v[1]} | Title: {v[2]} | Lang: {v[3]} | Created At: {v[4]}")

conn.close()
print("Saved dump to scratch/db_dump.txt")
