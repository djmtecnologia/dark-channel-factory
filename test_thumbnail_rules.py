import uuid
from agents.designer.thumbnail_generator import orchestrate_thumbnail_generation
from shared.logger import get_logger

logger = get_logger("TEST")

def test_thumbnail_orchestrator():
    print("🚀 Iniciando Teste do Orchestrator de Thumbnails (STRICT NEURODESIGN RULES)")
    test_id = uuid.uuid4()
    
    # Executa a geração (isso fará a chamada LLM para montar os prompts)
    # Como não temos garantia da API key do DALL-E aqui, isso testará a montagem dos prompts
    # e possivelmente a fallback
    try:
        result = orchestrate_thumbnail_generation(
            job_id=test_id,
            title="How AI Just Bankrupted the Middle Class",
            language="en-US",
            niche="Finance and AI"
        )
        
        print("\n✅ TESTE CONCLUÍDO COM SUCESSO!")
        print(f"- Variantes geradas: {len(result.get('thumbnails', []))}")
        
        for thumb in result.get('thumbnails', []):
            print(f"\n--- Variante {thumb.get('variant')} ---")
            print(f"Texto Selecionado: {thumb.get('text')}")
            print(f"Prompt Montado pelo LLM: {thumb.get('prompt')[:150]}...")
            print(f"Status API: {thumb.get('api_used')}")
            print(f"Score: {thumb.get('score')}")
            
    except Exception as e:
        print(f"❌ ERRO DURANTE O TESTE: {e}")

if __name__ == "__main__":
    test_thumbnail_orchestrator()
