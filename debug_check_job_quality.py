from shared.db import SessionLocal
from shared.models import VideoJob
import json

def check_last_jobs():
    session = SessionLocal()
    jobs = session.query(VideoJob).order_by(VideoJob.created_at.desc()).limit(3).all()
    
    for job in jobs:
        print(f"--- Job ID: {job.id} | Topic: {job.topic} | Status: {job.status} ---")
        if job.script_data:
            print(f"Description Length: {len(job.script_data.get('description', ''))}")
            print(f"Description Preview: {job.script_data.get('description', '')[:100]}...")
            print(f"Number of Scenes: {len(job.script_data.get('scenes', []))}")
        else:
            print("No script data found.")
        print("\n")
    session.close()

if __name__ == "__main__":
    check_last_jobs()
