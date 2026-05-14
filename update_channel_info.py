import os
from sqlmodel import Session, create_engine, select
from cloud_hub.models import Tenant, Channel
from dotenv import load_dotenv

load_dotenv()

def update_to_new_channel():
    print("🔄 [DB] Atualizando para o novo canal AI Business US...")
    
    engine = create_engine(os.getenv("DATABASE_URL"))
    
    with Session(engine) as session:
        # 1. Busca o Tenant antigo ou cria o novo
        # Tentamos achar pelo nome ou email antigo se existir
        tenant = session.exec(select(Tenant)).first() # Pega o primeiro tenant (você)
        
        if tenant:
            old_email = tenant.email
            tenant.email = "iabusinessus@gmail.com"
            tenant.name = "AI Business US"
            print(f"✅ Tenant atualizado: {old_email} -> {tenant.email}")
            
            # 2. Atualiza o canal principal
            channel = session.exec(select(Channel).where(Channel.tenant_id == tenant.id)).first()
            if channel:
                old_id = channel.youtube_channel_id
                channel.youtube_channel_id = "UCg90f4esWRxfpnsLLq0hzjg"
                channel.name = "AI Business US"
                print(f"✅ Canal atualizado: {old_id} -> {channel.youtube_channel_id}")
            else:
                # Se não houver canal, cria um novo
                new_channel = Channel(
                    tenant_id=tenant.id,
                    youtube_channel_id="UCg90f4esWRxfpnsLLq0hzjg",
                    name="AI Business US",
                    niche="ai",
                    language="en",
                    market_region="USA"
                )
                session.add(new_channel)
                print(f"✅ Novo canal criado para {tenant.name}")
        else:
            # Caso o banco esteja zerado
            tenant = Tenant(name="AI Business US", email="iabusinessus@gmail.com", plan_type="pro")
            session.add(tenant)
            session.commit()
            
            new_channel = Channel(
                tenant_id=tenant.id,
                youtube_channel_id="UCg90f4esWRxfpnsLLq0hzjg",
                name="AI Business US",
                niche="ai",
                language="en",
                market_region="USA"
            )
            session.add(new_channel)
            print("✅ Novo Tenant e Canal criados do zero.")

        session.commit()
        print("\n🚀 [SUCESSO] Banco de dados sincronizado com o novo canal!")

if __name__ == "__main__":
    update_to_new_channel()
