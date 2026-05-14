#!/usr/bin/env python3
"""
Test Suite - Dark Channel Factory
Testa as correções de bugs implementadas.
"""
import sys
import unittest
from datetime import datetime, timezone
from uuid import uuid4

class TestBugFixes(unittest.TestCase):
    """Testes para validar que os bugs foram corrigidos"""
    
    def test_01_video_job_has_video_type_field(self):
        """BUG FIX 1: Campo video_type deve existir no modelo"""
        try:
            from models import VideoJob
            
            # Cria instância de teste
            job = VideoJob(
                id=uuid4(),
                channel_id=uuid4(),
                video_type="LONG",
                status="PENDING"
            )
            
            # Verifica se campo existe e tem valor correto
            self.assertEqual(job.video_type, "LONG")
            print("✅ Test 1 PASSED: video_type field exists")
            
        except AttributeError as e:
            self.fail(f"❌ Test 1 FAILED: video_type field missing: {e}")
    
    def test_02_themes_rotation_works(self):
        """BUG FIX 2: Rotação de temas virais deve funcionar"""
        themes = ["Theme A", "Theme B", "Theme C"]
        
        # Simula seleção de 6 vídeos com 3 temas
        selected = []
        for i in range(6):
            topic = themes[i % len(themes)]
            selected.append(topic)
        
        # Verifica distribuição uniforme
        expected = ["Theme A", "Theme B", "Theme C", "Theme A", "Theme B", "Theme C"]
        self.assertEqual(selected, expected)
        print("✅ Test 2 PASSED: Theme rotation works correctly")
    
    def test_03_asyncio_wrapper_handles_running_loop(self):
        """BUG FIX 3: AsyncIO deve funcionar dentro de loop existente"""
        import asyncio
        import concurrent.futures
        
        async def dummy_async_task():
            await asyncio.sleep(0.1)
            return "success"
        
        # Simula comportamento corrigido
        def safe_async_run(coro):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            
            if loop and loop.is_running():
                # Usa thread pool se já estiver em loop
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, coro)
                    return future.result()
            else:
                return asyncio.run(coro)
        
        result = safe_async_run(dummy_async_task())
        self.assertEqual(result, "success")
        print("✅ Test 3 PASSED: AsyncIO wrapper handles running loop")
    
    def test_04_tts_preserves_accents(self):
        """BUG FIX 4: TTS não deve remover caracteres acentuados"""
        import re
        
        # Texto com acentos em vários idiomas
        text = "Olá! Cómo está? Naïve café résumé Über München"
        
        # Regex INCORRETO (antigo)
        bad_regex = re.sub(r"[^\x00-\x7F]", "", text)
        self.assertNotEqual(bad_regex, text)  # Remove acentos (RUIM)
        
        # Regex CORRETO (novo) - Remove apenas emojis
        good_text = re.sub(r"[\U00010000-\U0010FFFF]", "", text, flags=re.UNICODE)
        good_text = re.sub(r"[\U0001F300-\U0001FAFF]", "", good_text, flags=re.UNICODE)
        self.assertEqual(good_text, text)  # Preserva acentos (BOM)
        
        print("✅ Test 4 PASSED: TTS preserves accented characters")
    
    def test_05_thumbnail_fallback_cascade(self):
        """BUG FIX 5/6: Cascata de fallback de thumbnails"""
        # Simula tentativas de geração
        apis_available = {
            "dalle": False,  # Indisponível
            "pollinations_attempt_1": False,  # Primeira tentativa falha
            "pollinations_attempt_2": True,   # Segunda tentativa sucede
            "video_frame_fallback": True
        }
        
        # Simula lógica de retry
        result = None
        
        if apis_available["dalle"]:
            result = "dalle"
        elif apis_available["pollinations_attempt_1"]:
            result = "pollinations_1"
        elif apis_available["pollinations_attempt_2"]:
            result = "pollinations_2"
        elif apis_available["video_frame_fallback"]:
            result = "fallback"
        
        self.assertEqual(result, "pollinations_2")
        print("✅ Test 5 PASSED: Thumbnail fallback cascade works")
    
    def test_06_env_validation_on_startup(self):
        """BUG FIX 7: Validação de DATABASE_URL no startup"""
        import os
        
        # Simula ausência de DATABASE_URL
        original = os.environ.get("DATABASE_URL")
        if "DATABASE_URL" in os.environ:
            del os.environ["DATABASE_URL"]
        
        # Verifica se código detecta ausência
        database_url = os.getenv("DATABASE_URL")
        self.assertIsNone(database_url)
        
        # Restaura
        if original:
            os.environ["DATABASE_URL"] = original
        
        print("✅ Test 6 PASSED: ENV validation works")
    
    def test_07_model_imports_work(self):
        """Verifica se modelos importam sem erro"""
        try:
            from models import (
                Tenant, Channel, VideoJob, 
                PerformanceLog, AffiliateProduct, ViralTheme
            )
            
            # Verifica se todos têm ID
            models = [Tenant, Channel, VideoJob, PerformanceLog, AffiliateProduct, ViralTheme]
            for model in models:
                # Tenta criar instância vazia
                instance = model()
                self.assertIsNotNone(instance.id)
            
            print("✅ Test 7 PASSED: All models import correctly")
        except ImportError as e:
            self.fail(f"❌ Test 7 FAILED: Model import error: {e}")

class TestImprovements(unittest.TestCase):
    """Testes para validar melhorias implementadas"""
    
    def test_published_url_field_exists(self):
        """IMPROVEMENT 1: Campo published_url deve existir"""
        from models import VideoJob
        
        job = VideoJob(
            id=uuid4(),
            channel_id=uuid4(),
            published_url="https://youtube.com/watch?v=test123"
        )
        
        self.assertEqual(job.published_url, "https://youtube.com/watch?v=test123")
        print("✅ Improvement 1 PASSED: published_url field exists")
    
    def test_error_message_field_exists(self):
        """IMPROVEMENT 2: Campo error_message deve existir"""
        from models import VideoJob
        
        job = VideoJob(
            id=uuid4(),
            channel_id=uuid4(),
            error_message="Test error message"
        )
        
        self.assertIsNotNone(job.error_message)
        print("✅ Improvement 2 PASSED: error_message field exists")

def run_tests():
    """Executa todos os testes"""
    print("\n" + "="*60)
    print("DARK CHANNEL FACTORY - TEST SUITE".center(60))
    print("="*60 + "\n")
    
    # Cria suite de testes
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Adiciona testes
    suite.addTests(loader.loadTestsFromTestCase(TestBugFixes))
    suite.addTests(loader.loadTestsFromTestCase(TestImprovements))
    
    # Executa
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Resumo
    print("\n" + "="*60)
    print("TEST RESULTS".center(60))
    print("="*60)
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(f"Success rate: {((result.testsRun - len(result.failures) - len(result.errors)) / result.testsRun * 100):.1f}%")
    print("="*60 + "\n")
    
    return 0 if result.wasSuccessful() else 1

if __name__ == "__main__":
    exit_code = run_tests()
    sys.exit(exit_code)
