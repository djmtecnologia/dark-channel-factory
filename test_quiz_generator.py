import os
import sys
from PIL import Image

sys.stdout.reconfigure(encoding='utf-8')

# Adiciona diretório raiz do projeto para importação
sys.path.append(os.path.abspath("."))
from desktop_node import generate_quiz_frame

def test_quiz_localization():
    print("🧠 [TESTE QUIZ] Iniciando testes de geração e localização de frames de Quiz...")
    
    # Criar pasta para salvar os testes visuais
    test_dir = "test_assets"
    os.makedirs(test_dir, exist_ok=True)
    
    # Caso 1: Inglês (Chave 'answers')
    quiz_data_en = {
        "question": "What telemetry tool is widely used by F1 teams?",
        "answers": ["FastF1", "BeautifulSoup", "PyTrends"],
        "correct_index": 0
    }
    job_data_en = {"language": "en-US"}
    
    # Caso 2: Português (Chave 'respostas')
    quiz_data_pt = {
        "pergunta": "Qual piloto venceu o GP de Mônaco em 2026?",
        "respostas": ["Lewis Hamilton", "Charles Leclerc", "Max Verstappen"],
        "correct_index": 1
    }
    job_data_pt = {"language": "pt-BR"}
    
    # Caso 3: Espanhol (Sem opções no roteiro - Fallback traduzido)
    quiz_data_es = {
        "question": "¿Quién tiene el récord de campeonatos en la F1?",
        "correct_index": 0
    }
    job_data_es = {"language": "es-ES"}

    # Executar e gerar os frames
    print("  🎨 Gerando frame do Quiz em Inglês...")
    path_en = generate_quiz_frame(quiz_data_en, "en_test", job_data_en)
    
    print("  🎨 Gerando frame do Quiz em Português...")
    path_pt = generate_quiz_frame(quiz_data_pt, "pt_test", job_data_pt)
    
    print("  🎨 Gerando frame do Quiz em Espanhol (Fallback traduzido)...")
    path_es = generate_quiz_frame(quiz_data_es, "es_test", job_data_es)
    
    # Validar existências
    assert path_en and os.path.exists(path_en), "Erro ao gerar frame em Inglês!"
    assert path_pt and os.path.exists(path_pt), "Erro ao gerar frame em Português!"
    assert path_es and os.path.exists(path_es), "Erro ao gerar frame em Espanhol!"
    
    # Copiar arquivos gerados para a pasta de testes para inspeção fácil do usuário
    import shutil
    shutil.copy2(path_en, os.path.join(test_dir, "quiz_en_test.jpg"))
    shutil.copy2(path_pt, os.path.join(test_dir, "quiz_pt_test.jpg"))
    shutil.copy2(path_es, os.path.join(test_dir, "quiz_es_test.jpg"))
    
    print("\n" + "="*50)
    print("🚀 [SUCESSO] Todos os frames de Quiz foram gerados com sucesso!")
    print("Verifique os arquivos visuais gerados na pasta 'test_assets/':")
    print(" - quiz_en_test.jpg (Inglês: '🧠 QUICK BRAIN TEST' + opções dinâmicas)")
    print(" - quiz_pt_test.jpg (Português: '🧠 TESTE DE CÉREBRO RÁPIDO' + opções dinâmicas)")
    print(" - quiz_es_test.jpg (Espanhol: '🧠 TEST DE CEREBRO RÁPIDO' + fallbacks 'Opción A/B/C/D')")
    print("="*50 + "\n")

if __name__ == "__main__":
    test_quiz_localization()
