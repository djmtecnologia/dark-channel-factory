import os
import uuid
from sqlmodel import Session, create_engine, SQLModel
from agents.monetization.clickbank_scout import ClickBankScout
from agents.monetization.digistore_scout import DigistoreScout
from dotenv import load_dotenv

load_dotenv()

def test_affiliate_mining():
    print("💰 [TESTE] Validando Motores de Monetização...")
    
    # Setup de Banco em Memória
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    
    with Session(engine) as session:
        dummy_tenant_id = uuid.uuid4()
        topic = "AI Wealth and Financial Automation"
        niche = "ai"
        lang = "en"

        print(f"\n🔎 Tópico do Vídeo: '{topic}'")
        print("-" * 45)

        # 1. Teste ClickBank
        print("🌍 [MOTOR 1] Testando ClickBank Scout...")
        try:
            scout_cb = ClickBankScout(session)
            prod_cb = scout_cb.find_and_cache_product(topic, niche, lang, dummy_tenant_id)
            if prod_cb:
                print(f"✅ ClickBank Winner: {prod_cb.product_name}")
                print(f"🔗 Link Gerado: {prod_cb.hotlink_base}")
            else:
                print("❌ ClickBank não retornou produtos.")
        except Exception as e:
            print(f"⚠️ Erro no ClickBank: {e}")

        print("-" * 45)

        # 2. Teste Digistore24
        print("🇪🇺 [MOTOR 2] Testando Digistore24 Scout...")
        try:
            scout_ds = DigistoreScout(session)
            prod_ds = scout_ds.find_and_cache_product(topic, niche, lang, dummy_tenant_id)
            if prod_ds:
                print(f"✅ Digistore24 Winner: {prod_ds.product_name}")
                print(f"🔗 Link Gerado: {prod_ds.hotlink_base}")
                
                # Validação de ID
                aff_id = os.getenv("DIGISTORE_AFFILIATE_ID")
                if aff_id and aff_id in prod_ds.hotlink_base:
                    print(f"💎 Verificação de ID: OK ({aff_id} detectado no link!)")
                else:
                    print("⚠️ Alerta: ID de afiliado não detectado no link da Digistore.")
            else:
                print("❌ Digistore24 não retornou produtos.")
        except Exception as e:
            print(f"⚠️ Erro na Digistore24: {e}")

    print("\n" + "="*45)
    print("🚀 FIM DO TESTE DE MONETIZAÇÃO")
    print("="*45)

if __name__ == "__main__":
    test_affiliate_mining()
