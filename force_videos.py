import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

print("==============================================")
print("🧨 INJETOR DE ROTEIROS (FORÇANDO PRODUÇÃO)")
print("==============================================")

if not DATABASE_URL:
    print("❌ ABORTANDO: DATABASE_URL não configurada no .env!")
    exit(1)

engine = create_engine(DATABASE_URL)

# Safety confirmation before destructive SQL
print("\n⚠️  ATENÇÃO: Este script irá:")
print("   1. Apagar TODOS os vídeos da pasta 'videos_finais'")
print("   2. Resetar TODOS os jobs não-publicados para PENDING")
print("   3. Apagar TODO o script_data desses jobs\n")

confirm = input("❗ Tem certeza? Digite 'SIM' para continuar: ").strip().upper()
if confirm != "SIM":
    print("Operacao cancelada.")
    exit(0)

try:
    import shutil
    videos_dir = os.path.join(os.path.dirname(__file__), "videos_finais")
    if os.path.exists(videos_dir):
        print(f"🗑️ Excluindo todos os vídeos antigos da pasta {videos_dir}...")
        shutil.rmtree(videos_dir)
        os.makedirs(videos_dir, exist_ok=True)
        print("✅ Pasta limpa com sucesso.")

    with engine.connect() as conn:
        print("⏳ Enganando o sistema e movendo os vídeos pequenos para 'ontem'...")
        # Move os vídeos criados hoje para o passado
        conn.execute(text("UPDATE video_jobs SET created_at = created_at - INTERVAL '1 day' WHERE created_at >= CURRENT_DATE;"))
        
        print("🔄 Apagando os scripts ruins para forçar a IA a reescrever todos de novo...")
        # Reseta as tarefas PENDENTES apagando o script_data ruim do banco para que o Copywriter trabalhe novamente
        conn.execute(text("UPDATE video_jobs SET script_data = NULL, status = 'PENDING' WHERE status != 'PUBLISHED';"))
        
        conn.commit()
        print("✅ Sucesso! O Orquestrador agora acha que não publicou nada hoje.")
        print("\n👉 AÇÃO NECESSÁRIA: Feche a janela do SERVIÇO e inicie ele novamente no RUN_FACTORY.bat.")
        print("Ele vai criar imediatamente de 10 a 15 vídeos novos (agora com 15 minutos e shorts multiplicados).")
except Exception as e:
    print(f"❌ Erro ao forçar produção: {e}")
