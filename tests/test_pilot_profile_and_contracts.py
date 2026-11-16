"""
Testes Isolados: Perfil PILOT, Contratos de Cena e Isolamento de Banco
======================================================================
Valida todos os critérios da Fase 1 em ambiente 100% isolado (SQLite temporário).
Não conecta ao banco Neon nem ao banco de produção.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

# ─────────────────────────────────────────────────────────────
# 1. Configuração de Isolamento Total (Sem Neon, Sem Rede)
# ─────────────────────────────────────────────────────────────
TEMP_TEST_DIR = tempfile.mkdtemp()
TEST_DB_PATH = Path(TEMP_TEST_DIR) / "isolated_test_factory.db"

os.environ["DATABASE_URL"] = ""  # Garante desconexão total do Neon PG
os.environ["SQLITE_PATH"] = str(TEST_DB_PATH)
os.environ["MOCK_LLM"] = "true"
os.environ["SKIP_UPLOAD"] = "true"
os.environ["DRY_RUN"] = "true"

from shared.production_profiles import (
    PROFILES,
    ProductionProfileType,
    ProductionProfileConfig,
    resolve_profile,
    validate_duration,
    validate_script_word_count,
    validate_scene_count,
)
from shared.scene_normalizer import normalize_scene, normalize_scenes
from shared.models import Scene, SceneType, Emotion
from shared.script_validator import validate_and_fix_script, check_visual_cues
from agents.validator.qc_agent import ValidatorAgent
from agents.content_validator.agent import run_content_validator_agent
from cloud_hub.models import VideoJob, Channel, Tenant
from database.db_manager import get_db, _get_db_url


class TestProductionProfiles(unittest.TestCase):
    """Testes de parametrização e validação de perfis de produção."""

    def test_db_isolation_no_neon(self):
        """Garante que o banco de dados está isolado e não aponta para o Neon."""
        self.assertEqual(_get_db_url(), "")
        with get_db() as conn:
            self.assertIsNotNone(conn)
        print("✅ DB Isolation: Conexão estritamente local em SQLite.")

    # ── Testes PILOT Duração ───────────────────────────────────

    def test_pilot_300s_accepted(self):
        """PILOT com 300 segundos deve ser aprovado."""
        passed, err = validate_duration(300.0, profile="PILOT_5MIN")
        self.assertTrue(passed)
        self.assertIsNone(err)

    def test_pilot_270s_boundary_minimum_accepted(self):
        """PILOT com 270 segundos (limite inferior) deve ser aprovado."""
        passed, err = validate_duration(270.0, profile="PILOT_5MIN")
        self.assertTrue(passed)
        self.assertIsNone(err)

    def test_pilot_330s_boundary_maximum_accepted(self):
        """PILOT com 330 segundos (limite superior) deve ser aprovado."""
        passed, err = validate_duration(330.0, profile="PILOT_5MIN")
        self.assertTrue(passed)
        self.assertIsNone(err)

    def test_pilot_below_minimum_rejected(self):
        """PILOT abaixo do mínimo (269.9s) deve ser reprovado."""
        passed, err = validate_duration(269.9, profile="PILOT_5MIN")
        self.assertFalse(passed)
        self.assertIn("abaixo do mínimo para PILOT_5MIN", err)

    def test_pilot_above_maximum_rejected(self):
        """PILOT acima do máximo (330.1s) deve ser reprovado."""
        passed, err = validate_duration(330.1, profile="PILOT_5MIN")
        self.assertFalse(passed)
        self.assertIn("acima do máximo para PILOT_5MIN", err)

    # ── Testes PILOT Roteiro / Palavras ───────────────────────

    def test_pilot_script_600_words_accepted(self):
        """Roteiro PILOT com 600 palavras (mínimo) deve ser aprovado."""
        passed, err = validate_script_word_count(600, profile="PILOT_5MIN", validator_type="script")
        self.assertTrue(passed)
        self.assertIsNone(err)

    def test_pilot_script_850_words_accepted(self):
        """Roteiro PILOT com 850 palavras (máximo) deve ser aprovado."""
        passed, err = validate_script_word_count(850, profile="PILOT_5MIN", validator_type="script")
        self.assertTrue(passed)
        self.assertIsNone(err)

    def test_pilot_script_700_words_accepted(self):
        """Roteiro PILOT com ~700 palavras (nominal) deve ser aprovado."""
        passed, err = validate_script_word_count(700, profile="PILOT_5MIN", validator_type="script")
        self.assertTrue(passed)
        self.assertIsNone(err)

    def test_pilot_script_below_minimum_rejected(self):
        """Roteiro PILOT abaixo do mínimo (599 palavras) deve ser reprovado."""
        passed, err = validate_script_word_count(599, profile="PILOT_5MIN", validator_type="script")
        self.assertFalse(passed)
        self.assertIn("Script too short: 599 words (minimum 600", err)

    # ── Testes Outros Perfis ───────────────────────────────────

    def test_standard_profile(self):
        """Perfil STANDARD_10MIN: 480-720s e 1200-1600 palavras."""
        # Duração
        self.assertTrue(validate_duration(480.0, profile="STANDARD_10MIN")[0])
        self.assertTrue(validate_duration(600.0, profile="STANDARD_10MIN")[0])
        self.assertTrue(validate_duration(720.0, profile="STANDARD_10MIN")[0])
        self.assertFalse(validate_duration(479.0, profile="STANDARD_10MIN")[0])
        self.assertFalse(validate_duration(721.0, profile="STANDARD_10MIN")[0])

        # Palavras
        self.assertTrue(validate_script_word_count(1200, profile="STANDARD_10MIN")[0])
        self.assertFalse(validate_script_word_count(1199, profile="STANDARD_10MIN")[0])

    def test_documentary_profile(self):
        """Perfil DOCUMENTARY_20MIN: 900-1800s e 2200-4000 palavras."""
        # Duração
        self.assertTrue(validate_duration(900.0, profile="DOCUMENTARY_20MIN")[0])
        self.assertTrue(validate_duration(1500.0, profile="DOCUMENTARY_20MIN")[0])
        self.assertTrue(validate_duration(1800.0, profile="DOCUMENTARY_20MIN")[0])
        self.assertFalse(validate_duration(899.0, profile="DOCUMENTARY_20MIN")[0])
        self.assertFalse(validate_duration(1801.0, profile="DOCUMENTARY_20MIN")[0])

        # Palavras
        self.assertTrue(validate_script_word_count(2200, profile="DOCUMENTARY_20MIN")[0])
        self.assertFalse(validate_script_word_count(2199, profile="DOCUMENTARY_20MIN")[0])

    def test_shorts_profile(self):
        """Perfil SHORTS: 20-59s e vertical."""
        self.assertTrue(validate_duration(20.0, profile="SHORTS")[0])
        self.assertTrue(validate_duration(45.0, profile="SHORTS")[0])
        self.assertTrue(validate_duration(59.0, profile="SHORTS")[0])
        self.assertFalse(validate_duration(19.0, profile="SHORTS")[0])
        self.assertFalse(validate_duration(60.0, profile="SHORTS")[0])

        prof = resolve_profile(profile_name="SHORTS")
        self.assertTrue(prof.is_vertical)

    # ── Testes Comportamento Legado (Sem Perfil Explícito) ──────

    def test_jobs_without_explicit_profile_legacy(self):
        """Jobs sem perfil explícito preservam o comportamento legado (LONG >= 480s / SHORT >= 20s)."""
        # LONG legado não tem teto máximo no validador
        passed_long, _ = validate_duration(1200.0, profile=None, video_type="LONG")
        self.assertTrue(passed_long)

        # LONG legado reprova abaixo de 480s
        passed_short_fail, err = validate_duration(300.0, profile=None, video_type="LONG")
        self.assertFalse(passed_short_fail)
        self.assertIn("Mínimo: 480.0s", err)

        # Palavras legado: 900 palavras para script_validator
        self.assertTrue(validate_script_word_count(900, profile=None, validator_type="script")[0])
        self.assertFalse(validate_script_word_count(899, profile=None, validator_type="script")[0])


class TestSceneNormalization(unittest.TestCase):
    """Testes de normalização e compatibilização do contrato de cenas."""

    def test_b_roll_visual_compatibility(self):
        """Cena com b_roll_visual deve normalizar preenchendo b_roll_query e sem fallback AI technology."""
        raw_scene = {
            "narration": "In the year 2026, autonomous neural networks completely reshaped software engineering.",
            "b_roll_visual": "futuristic data center glowing server racks dark room cinematic lighting",
            "timestamp": "0:00-0:05",
        }
        normalized = normalize_scene(raw_scene, fallback_topic="Artificial Intelligence")
        self.assertEqual(normalized["b_roll_visual"], "futuristic data center glowing server racks dark room cinematic lighting")
        self.assertEqual(normalized["b_roll_query"], "futuristic data center glowing server racks dark room cinematic lighting")
        self.assertNotEqual(normalized["b_roll_visual"], "AI technology")

    def test_b_roll_query_compatibility(self):
        """Cena com b_roll_query deve normalizar preenchendo b_roll_visual e sem fallback AI technology."""
        raw_scene = {
            "narration": "Wall Street automated over eighty percent of high frequency trades.",
            "b_roll_query": "stock market traders trading floor dynamic monitors fast paced",
            "duration_s": 6.5,
        }
        normalized = normalize_scene(raw_scene, fallback_topic="Wall Street")
        self.assertEqual(normalized["b_roll_visual"], "stock market traders trading floor dynamic monitors fast paced")
        self.assertEqual(normalized["b_roll_query"], "stock market traders trading floor dynamic monitors fast paced")
        self.assertNotEqual(normalized["b_roll_visual"], "AI technology")

    def test_both_b_roll_fields_preserved(self):
        """Quando ambos os campos existem com valores distintos, ambos são preservados."""
        raw_scene = {
            "text": "Quantum computers broke legacy encryption.",
            "b_roll_visual": "quantum processor close up laser cooling glowing blue",
            "b_roll_query": "quantum computer laboratory supercomputing",
            "timestamp": "0:10-0:15",
        }
        normalized = normalize_scene(raw_scene)
        self.assertEqual(normalized["b_roll_visual"], "quantum processor close up laser cooling glowing blue")
        self.assertEqual(normalized["b_roll_query"], "quantum computer laboratory supercomputing")

    def test_old_scenes_compatibility(self):
        """Cenas legadas com formato antigo (text, duration_s, visual) devem ser normalizadas com segurança."""
        old_scene = {
            "id": 1,
            "text": "This is an older legacy format scene without narration or timestamp keys.",
            "duration_s": 8.0,
            "visual": "satellite orbiting earth space dark background",
            "emotion": "authority",
            "sfx_cue": "whoosh",
        }
        normalized = normalize_scene(old_scene, fallback_topic="Space Technology")
        self.assertEqual(normalized["narration"], old_scene["text"])
        self.assertEqual(normalized["text"], old_scene["text"])
        self.assertEqual(normalized["b_roll_visual"], "satellite orbiting earth space dark background")
        self.assertEqual(normalized["b_roll_query"], "satellite orbiting earth space dark background")
        self.assertEqual(normalized["duration_s"], 8.0)
        self.assertEqual(normalized["emotion"], "authority")
        self.assertEqual(normalized["sfx_cue"], "whoosh")

    def test_no_silent_ai_technology_fallback(self):
        """Cena sem campos de B-roll deve usar o fallback_topic e nunca 'AI technology' silencioso."""
        empty_scene = {
            "narration": "A secret meeting occurred in the heart of Zurich.",
            "timestamp": "0:00-0:06"
        }
        normalized = normalize_scene(empty_scene, fallback_topic="Swiss Banking Secrets")
        self.assertEqual(normalized["b_roll_visual"], "Swiss Banking Secrets")
        self.assertEqual(normalized["b_roll_query"], "Swiss Banking Secrets")
        self.assertNotEqual(normalized["b_roll_visual"], "AI technology")

    def test_pydantic_scene_model_compatibility(self):
        """Modelo Scene do Pydantic deve reconciliar b_roll_visual e b_roll_query perfeitamente."""
        scene_obj = Scene(
            id=1,
            scene_type=SceneType.HOOK,
            text="The global economy shifted overnight.",
            duration_s=7.0,
            b_roll_query="stock market crash red arrows tickers",
        )
        self.assertEqual(scene_obj.b_roll_visual, "stock market crash red arrows tickers")
        self.assertEqual(scene_obj.narration, "The global economy shifted overnight.")


class TestValidatorsIntegration(unittest.TestCase):
    """Testes integrados dos validadores com perfis e contratos normalizados."""

    def test_script_validator_with_pilot_profile(self):
        """Validador de roteiro com perfil PILOT (700 palavras) deve passar com sucesso."""
        # 14 cenas distintas com ~50 palavras cada, totalizando ~700 palavras (dentro do intervalo 600-850)
        scene_paragraphs = [
            "Nobody saw this coming in the global tech landscape. Autonomous artificial intelligence networks are rapidly advancing beyond human expectations and redefining productivity standards across every sector. Software systems that once took months to build are now synthesized in hours through cooperative multi agent orchestration architectures operating with unprecedented mathematical precision and reliability.",
            "The initial signs began appearing quietly across leading financial institutions. Quantitative engineers noticed automated trading algorithms generating novel investment strategies that consistently outperformed seasoned human analysts. These autonomous agents detected microscopic liquidity imbalances across distributed global exchanges in milliseconds, creating a massive competitive advantage for early institutional adopters.",
            "Traditional corporate management structures were completely unprepared for this structural shift. Entire operational departments that previously required dozens of human coordinators suddenly operated autonomously using lightweight neural agent orchestrators. Communication bottlenecks disappeared as intelligent workflows routed critical decisions directly to specialized autonomous microservices without human delay or administrative friction.",
            "Economic productivity metrics began showing exponential acceleration across emerging markets. Startups founded by solitary developers were generating millions in annual revenue without hiring conventional administrative or operational personnel. The barrier to entry for building scalable global digital products virtually vanished, allowing rapid iteration cycles and unprecedented market responsiveness.",
            "However, this unprecedented transformation introduced complex systemic vulnerabilities across digital infrastructure. Legacy cybersecurity protocols failed to detect subtle automated attacks executed by cooperating adversarial machine learning agents. Security teams had to reinvent defensive postures using predictive neural sentinels capable of neutralizing zero day vulnerabilities in real time.",
            "Infrastructure engineers were forced to redesign distributed computing architectures from the ground up. High performance server clusters were deployed globally to handle massive real-time token throughput demands. Latency became the critical battleground as edge computing nodes processed neural inference locally on user devices with remarkable energy efficiency.",
            "Meanwhile, international regulatory agencies scrambled to comprehend the velocity of these developments. Proposed legislative frameworks were rendered obsolete before committees could finalize their preliminary documentation. The boundary between technological innovation and national economic security became the primary focus of international diplomatic summits and trade policy discussions.",
            "The historical division between technical software architects and domain specialists began disappearing entirely. Modern software creators function as strategic conductors orchestrating fleets of autonomous digital specialists. What you don't know yet is how quickly these tools are democratizing capabilities that once belonged exclusively to tech giants.",
            "Furthermore, continental energy grids experienced sudden surges in power consumption. Green energy facilities and modular nuclear reactors became essential partners for next generation hyperscale compute centers. Energy efficiency and silicon cooling technologies emerged as the ultimate physical bottlenecks determining the pace of artificial intelligence deployment globally.",
            "Education and career training shifted toward systems thinking, critical analysis, and architectural oversight. The ability to direct intelligent software models became the single most valuable cognitive skill in the world. Universities overhauled computer science curricula to focus on autonomous multi-agent coordination rather than basic programming syntax.",
            "Scientific laboratories leveraged these tools to solve decades-old biochemical and pharmaceutical challenges. Novel molecular compounds and personalized medical treatments were designed and validated in fractions of the time previously required. Autonomous robotic laboratories synthesized experimental compounds around the clock, accelerating the discovery of life saving therapeutic solutions.",
            "Small businesses in emerging markets gained access to enterprise grade operational capabilities. Global trade patterns diversified as localized entrepreneurs deployed automated translation, customer acquisition, and logistics coordination systems. Coming up next, we will reveal the exact framework that industry leaders are using to capitalize on this paradigm shift.",
            "As we approach the culmination of this technological epoch, one realization remains crystal clear to researchers. The future belongs to those who adapt and build alongside intelligent autonomous systems today. Those who hesitate risk falling behind in an economy moving at the speed of automated algorithmic computation.",
            "Subscribe to the channel right now to stay ahead of the artificial intelligence revolution and never miss our deep dive documentary analysis. Leave a comment below sharing your thoughts on how autonomous systems are transforming your industry today."
        ]

        scenes = []
        for i, text in enumerate(scene_paragraphs):
            scenes.append({
                "narration": text,
                "b_roll_visual": f"cinematic footage scene {i} documentary lighting high quality cinematic B-roll",
                "timestamp": f"0:{i*5:02d}-0:{(i+1)*5:02d}",
                "visual_cues": [{"t": 0, "type": "zoom_in"}],
            })

        script_data = {
            "title": "The AI Revolution 2026",
            "production_profile": "PILOT_5MIN",
            "scenes": scenes,
        }

        cleaned, res = validate_and_fix_script(script_data, profile="PILOT_5MIN")
        self.assertTrue(res.passed, f"Validation failed with errors: {res.errors}")
        self.assertGreaterEqual(res.word_count, 600)
        self.assertLessEqual(res.word_count, 850)
        print(f"✅ Script Validator PILOT: Aprovado com {res.word_count} palavras e {len(cleaned['scenes'])} cenas.")

    def test_qc_agent_pilot_validation(self):
        """QC Agent aceita vídeo PILOT de 300s e reprova vídeos fora da faixa (260s e 340s)."""
        job_mock = {
            "id": str(uuid4()),
            "topic": "Pilot Test Topic",
            "video_type": "LONG",
            "production_profile": "PILOT_5MIN",
            "script_data": {"title": "Pilot Test Topic", "script_full": "Valid script text here " * 50},
        }

        # Simula validação de duração diretamente via ValidatorAgent logic
        val_300 = ValidatorAgent(session=None, job=job_mock, video_path="mock.mp4")
        self.assertIsNotNone(val_300.resolved_profile)
        self.assertEqual(val_300.resolved_profile.name, "PILOT_5MIN")

        # Testa validação de duração diretamente através da função centralizada
        self.assertTrue(validate_duration(300.0, profile=val_300.resolved_profile)[0])
        self.assertFalse(validate_duration(260.0, profile=val_300.resolved_profile)[0])
        self.assertFalse(validate_duration(340.0, profile=val_300.resolved_profile)[0])
        print("✅ QC Agent PILOT: Duração 300s aprovada; 260s e 340s corretamente reprovadas.")

    def test_content_validator_with_pilot_profile(self):
        """Content Validator Agent deve aprovar roteiro PILOT com 700 palavras sem exigir 1800."""
        scene_paragraphs = [
            "Nobody saw this coming in the global tech landscape. Autonomous artificial intelligence networks are rapidly advancing beyond human expectations and redefining productivity standards across every sector. Software systems that once took months to build are now synthesized in hours through cooperative multi agent orchestration architectures operating with unprecedented mathematical precision and reliability.",
            "The initial signs began appearing quietly across leading financial institutions. Quantitative engineers noticed automated trading algorithms generating novel investment strategies that consistently outperformed seasoned human analysts. These autonomous agents detected microscopic liquidity imbalances across distributed global exchanges in milliseconds, creating a massive competitive advantage for early institutional adopters.",
            "Traditional corporate management structures were completely unprepared for this structural shift. Entire operational departments that previously required dozens of human coordinators suddenly operated autonomously using lightweight neural agent orchestrators. Communication bottlenecks disappeared as intelligent workflows routed critical decisions directly to specialized autonomous microservices without human delay or administrative friction.",
            "Economic productivity metrics began showing exponential acceleration across emerging markets. Startups founded by solitary developers were generating millions in annual revenue without hiring conventional administrative or operational personnel. The barrier to entry for building scalable global digital products virtually vanished, allowing rapid iteration cycles and unprecedented market responsiveness.",
            "However, this unprecedented transformation introduced complex systemic vulnerabilities across digital infrastructure. Legacy cybersecurity protocols failed to detect subtle automated attacks executed by cooperating adversarial machine learning agents. Security teams had to reinvent defensive postures using predictive neural sentinels capable of neutralizing zero day vulnerabilities in real time.",
            "Infrastructure engineers were forced to redesign distributed computing architectures from the ground up. High performance server clusters were deployed globally to handle massive real-time token throughput demands. Latency became the critical battleground as edge computing nodes processed neural inference locally on user devices with remarkable energy efficiency.",
            "Meanwhile, international regulatory agencies scrambled to comprehend the velocity of these developments. Proposed legislative frameworks were rendered obsolete before committees could finalize their preliminary documentation. The boundary between technological innovation and national economic security became the primary focus of international diplomatic summits and trade policy discussions.",
            "The historical division between technical software architects and domain specialists began disappearing entirely. Modern software creators function as strategic conductors orchestrating fleets of autonomous digital specialists. What you don't know yet is how quickly these tools are democratizing capabilities that once belonged exclusively to tech giants.",
            "Furthermore, continental energy grids experienced sudden surges in power consumption. Green energy facilities and modular nuclear reactors became essential partners for next generation hyperscale compute centers. Energy efficiency and silicon cooling technologies emerged as the ultimate physical bottlenecks determining the pace of artificial intelligence deployment globally.",
            "Education and career training shifted toward systems thinking, critical analysis, and architectural oversight. The ability to direct intelligent software models became the single most valuable cognitive skill in the world. Universities overhauled computer science curricula to focus on autonomous multi-agent coordination rather than basic programming syntax.",
            "Scientific laboratories leveraged these tools to solve decades-old biochemical and pharmaceutical challenges. Novel molecular compounds and personalized medical treatments were designed and validated in fractions of the time previously required. Autonomous robotic laboratories synthesized experimental compounds around the clock, accelerating the discovery of life saving therapeutic solutions.",
            "Small businesses in emerging markets gained access to enterprise grade operational capabilities. Global trade patterns diversified as localized entrepreneurs deployed automated translation, customer acquisition, and logistics coordination systems. Coming up next, we will reveal the exact framework that industry leaders are using to capitalize on this paradigm shift.",
            "As we approach the culmination of this technological epoch, one realization remains crystal clear to researchers. The future belongs to those who adapt and build alongside intelligent autonomous systems today. Those who hesitate risk falling behind in an economy moving at the speed of automated algorithmic computation.",
            "Subscribe to the channel right now to stay ahead of the artificial intelligence revolution and never miss our deep dive documentary analysis. Leave a comment below sharing your thoughts on how autonomous systems are transforming your industry today."
        ]

        scenes = [
            Scene(
                id=i,
                scene_type=SceneType.HOOK if i == 0 else SceneType.SOLUTION,
                text=p,
                duration_s=20.0,
                b_roll_query="technology innovation futuristic computers software architecture",
            )
            for i, p in enumerate(scene_paragraphs)
        ]
        
        script_dict = {
            "job_id": str(uuid4()),
            "title": "Autonomous AI Engineering Revolution",
            "description": "Comprehensive documentary analysis on autonomous software engineering workflows and AI infrastructure.",
            "thumbnail_brief": "Dramatic developer terminal glowing with autonomous neural agent architecture.",
            "production_profile": "PILOT_5MIN",
            "niche_data": {
                "channel_id": "test_ch",
                "niche": "ai_tools",
                "topic": "Autonomous AI Engineering Revolution",
                "target_audience": "Tech enthusiasts and software engineers",
                "angle": "Fresh architectural perspective on autonomous multi-agent development",
                "content_gap": "Lack of realistic production engineering guides and practical benchmarks",
                "primary_keyword": "AI software engineering",
                "suggested_title": "Autonomous AI Engineering Revolution",
                "language": "en-US",
            },
            "scenes": [s.model_dump() for s in scenes],
        }

        # Executa content validator com mock LLM
        res = run_content_validator_agent(script_dict, save_db=False, forward=False, profile="PILOT_5MIN")
        self.assertTrue(res.passed, f"Content validator reprovou roteiro PILOT: {res}")
        print(f"✅ Content Validator PILOT: Aprovado com sucesso ({res.originality_score:.1f}% orig, {res.narrative_quality_score:.1f}% qual).")


if __name__ == "__main__":
    unittest.main()
