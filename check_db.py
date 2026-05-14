from sqlmodel import Session, create_engine, select
from models import Channel, VideoJob
import os
from dotenv import load_dotenv

load_dotenv()

engine = create_engine(os.getenv("DATABASE_URL"))

def list_channels():
    with Session(engine) as session:
        channels = session.exec(select(Channel)).all()
        print(f"Encontrados {len(channels)} canais:")
        for c in channels:
            print(f"- ID: {c.id} | Name: {c.name} | Region: {c.market_region} | Lang: {c.language}")

if __name__ == "__main__":
    list_channels()
