import os
from sqlmodel import Session, create_engine, select
from cloud_hub.models import VideoJob
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)

def check_last_job():
    with Session(engine) as session:
        job = session.exec(select(VideoJob).order_by(VideoJob.created_at.desc())).first()
        if job:
            print(f"ID: {job.id}")
            print(f"Status: {job.status}")
            print(f"Topic: {job.topic}")
            print(f"Script Data Keys: {list(job.script_data.keys()) if job.script_data else 'None'}")
            if job.script_data and 'scenes' in job.script_data:
                print(f"Number of scenes: {len(job.script_data['scenes'])}")
                if len(job.script_data['scenes']) > 0:
                    print(f"Sample Scene: {job.script_data['scenes'][0]}")
            else:
                print("No scenes in script_data")
        else:
            print("No jobs found.")

if __name__ == "__main__":
    check_last_job()
