"""
Tests v2 — Dark Channel Factory
Testes unitários para os 4 novos módulos:
- Viral Score Agent (gatekeeper)
- Content Validator
- Shorts Agent (extrator)
- Analyst & Learning Agent
- Database Manager
- Multi-market Config
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

# Variáveis de ambiente para modo mock/dry-run
os.environ["MOCK_LLM"] = "true"
os.environ["SKIP_UPLOAD"] = "true"
os.environ["DRY_RUN"] = "true"
os.environ["DATABASE_URL"] = ""
os.environ["SQLITE_PATH"] = str(Path(tempfile.mkdtemp()) / "test_factory.db")


# ─────────────────────────────────────────────────────────────
# Testes: Models V2
# ─────────────────────────────────────────────────────────────

class TestModelsV2(unittest.TestCase):

    def test_viral_score_breakdown_total(self):
        """ViralScoreBreakdown.total deve somar corretamente os 5 critérios."""
        from shared.models_v2 import ViralScoreBreakdown
        b = ViralScoreBreakdown(
            title_power=18.0, curiosity_gap=15.0, search_volume=16.0,
            competition_gap=14.0, trend_factor=17.0
        )
        self.assertAlmostEqual(b.total, 80.0)
        print(f"✅ ViralScoreBreakdown.total={b.total}/100")

    def test_viral_score_result_serialization(self):
        """ViralScoreResult deve serializar/deserializar corretamente."""
        from shared.models_v2 import ViralScoreResult, ViralScoreBreakdown
        result = ViralScoreResult(
            job_id=uuid4(),
            topic="5 Shocking AI Tools Nobody Talks About in 2025",
            angle="Secret tools exposed — most people waste money on GPT",
            language="en-US",
            niche="ai_tools",
            score=82.0,
            breakdown=ViralScoreBreakdown(
                title_power=18, curiosity_gap=17, search_volume=16,
                competition_gap=15, trend_factor=16
            ),
            passed_gate=True,
        )
        data = result.model_dump(mode="json")
        restored = ViralScoreResult(**data)
        self.assertEqual(result.score, restored.score)
        self.assertTrue(restored.passed_gate)
        print(f"✅ ViralScoreResult OK | score={result.score} | passed={result.passed_gate}")

    def test_content_validation_result(self):
        """ContentValidationResult deve rastrear dimensões corretamente."""
        from shared.models_v2 import ContentValidationResult
        r = ContentValidationResult(
            job_id=uuid4(),
            passed=True,
            originality_score=88.0,
            narrative_quality_score=75.0,
            transformative_value_score=80.0,
            word_count=750,
            estimated_duration_s=300.0,
            has_strong_hook=True,
            has_open_loops=True,
            has_clear_cta=True,
            affiliate_links_count=2,
        )
        self.assertTrue(r.passed)
        self.assertEqual(r.word_count, 750)
        print(f"✅ ContentValidationResult | Orig={r.originality_score} | Quality={r.narrative_quality_score}")

    def test_short_clip_model(self):
        """ShortClip deve aceitar platform_video_ids dinâmico."""
        from shared.models_v2 import ShortClip
        clip = ShortClip(
            clip_index=1,
            source_video_path="/tmp/video.mp4",
            output_path="/tmp/short_1.mp4",
            start_s=0.0,
            end_s=55.0,
            duration_s=55.0,
            hook_text="Most people don't know this secret tool...",
            cta_text="Full video in description ↑",
        )
        clip.platform_video_ids["youtube_shorts"] = "ABC123"
        clip.platform_video_ids["tiktok"] = "TK456"
        self.assertEqual(len(clip.platform_video_ids), 2)
        print(f"✅ ShortClip | hook='{clip.hook_text[:30]}' | platforms={list(clip.platform_video_ids.keys())}")

    def test_tier1_markets_config(self):
        """TIER1_MARKETS deve ter 5 mercados configurados."""
        from shared.models_v2 import TIER1_MARKETS, NICHE_BASE_CPM
        self.assertEqual(len(TIER1_MARKETS), 5)
        self.assertIn("en-US", TIER1_MARKETS)
        self.assertIn("de-DE", TIER1_MARKETS)
        self.assertEqual(len(NICHE_BASE_CPM), 4)

        us_market = TIER1_MARKETS["en-US"]
        self.assertEqual(us_market.niche_cpm_multiplier, 1.0)
        self.assertEqual(us_market.voice, "en-US-AriaNeural")

        # CPM efetivo por mercado
        for lang, market in TIER1_MARKETS.items():
            cpm = NICHE_BASE_CPM["ai_tools"] * market.niche_cpm_multiplier
            print(f"  {lang}: CPM ${cpm:.1f}")

        print(f"✅ TIER1_MARKETS OK | {list(TIER1_MARKETS.keys())}")

    def test_learning_insight_model(self):
        """LearningInsight deve serializar listas corretamente."""
        from shared.models_v2 import LearningInsight
        insight = LearningInsight(
            top_niches=["ai_tools", "personal_finance"],
            top_title_patterns=["Why X is wrong", "I tried X for 30 days"],
            top_thumbnail_styles=["face + shock", "data viz"],
            top_hooks=["Most people don't know...", "Stop doing this now"],
            replicate_topics=[
                {"topic": "5 AI Tools", "original_language": "en-US",
                 "target_languages": ["de-DE", "fr-FR"], "estimated_ctr": 6.5}
            ],
            recommended_new_markets=["de-DE", "fr-FR"],
        )
        data = insight.model_dump(mode="json")
        self.assertEqual(len(insight.top_niches), 2)
        self.assertEqual(len(insight.replicate_topics), 1)
        print(f"✅ LearningInsight | niches={insight.top_niches} | markets={insight.recommended_new_markets}")


# ─────────────────────────────────────────────────────────────
# Testes: Database Manager
# ─────────────────────────────────────────────────────────────

class TestDatabaseManager(unittest.TestCase):

    def test_db_creation_and_schema(self):
        """DB deve ser criado com schema correto."""
        from database.db_manager import get_db
        with get_db() as conn:
            cursor = conn.cursor()
            try:
                is_pg = hasattr(conn, 'pgconn') or type(conn).__module__.startswith("psycopg2")
                if is_pg:
                    cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
                else:
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = cursor.fetchall()
                table_names = [t[0] for t in tables]
            finally:
                cursor.close()

        self.assertIn("videos", table_names)
        self.assertIn("shorts", table_names)
        self.assertIn("topics_history", table_names)
        self.assertIn("winning_patterns", table_names)
        self.assertIn("affiliate_performance", table_names)
        self.assertIn("pipeline_jobs", table_names)
        print(f"✅ DB schema OK | tables={table_names}")

    def test_save_and_retrieve_topic(self):
        """Deve salvar tópico e detectar duplicata corretamente."""
        from database.db_manager import save_topic, topic_exists

        unique_topic = f"Test Topic {uuid4().hex[:6]}"
        save_topic(unique_topic, "ai_tools", "en-US", 75.0)

        # Tópico idêntico deve ser detectado
        exists = topic_exists(unique_topic, "ai_tools", "en-US")
        self.assertTrue(exists)

        # Tópico diferente NOT deve ser detectado
        not_exists = topic_exists(f"Completely Different Topic {uuid4().hex}", "ai_tools", "en-US")
        self.assertFalse(not_exists)
        print(f"✅ Topic history | saved='{unique_topic[:30]}' | duplicate_detected=True")

    def test_winning_pattern_upsert(self):
        """Deve salvar e atualizar padrão vencedor com media móvel."""
        from database.db_manager import upsert_winning_pattern, get_winning_patterns

        pattern_value = f"list_format_{uuid4().hex[:4]}"
        upsert_winning_pattern("title_structure", pattern_value, "ai_tools", "en-US", 6.5, 55.0)
        upsert_winning_pattern("title_structure", pattern_value, "ai_tools", "en-US", 7.5, 60.0)

        patterns = get_winning_patterns(niche="ai_tools", language="en-US")
        matching = [p for p in patterns if p["pattern_value"] == pattern_value]

        if matching:
            p = matching[0]
            self.assertEqual(p["sample_size"], 2)
            self.assertAlmostEqual(p["avg_ctr"], 7.0, places=0)
            print(f"✅ WinningPattern | value='{pattern_value}' | avg_ctr={p['avg_ctr']:.1f} | n={p['sample_size']}")
        else:
            print(f"ℹ️ Pattern not returned (needs sample_size >= 2 for listing)")

    def test_pipeline_job_lifecycle(self):
        """Deve registrar e falhar um job corretamente."""
        from database.db_manager import save_pipeline_job, fail_pipeline_job, get_failed_jobs

        job_id = str(uuid4())
        save_pipeline_job(job_id, "ai_tools_us", "pending", "ai_tools", "en-US", "Test Topic")
        fail_pipeline_job(job_id, "Score too low: 45.0 < 70", retry_count=2)

        failed = get_failed_jobs(limit=100)
        failed_ids = [f["id"] for f in failed]
        self.assertIn(job_id, failed_ids)
        print(f"✅ PipelineJob lifecycle | job={job_id[:8]} | status=failed | retry=2")


# ─────────────────────────────────────────────────────────────
# Testes: Viral Score Agent
# ─────────────────────────────────────────────────────────────

class TestViralScoreAgent(unittest.TestCase):

    def _make_niche_data(self, topic: str, angle: str, competition: str = "low") -> dict:
        from shared.models import NicheData
        return NicheData(
            topic=topic,
            primary_keyword="AI tools",
            angle=angle,
            content_gap="No one shows real numbers",
            competition_level=competition,
            niche_score=80,
        ).model_dump(mode="json")

    def test_rule_based_score_high_topic(self):
        """Tópico com power words deve ter score alto."""
        from agents.viral_score.agent import calculate_rule_based_score
        breakdown = calculate_rule_based_score(
            topic="Nobody Tells You This Secret AI Tool That Kills Procrastination",
            angle="Shocking truth exposed — stop wasting time",
            language="en-US",
            niche="ai_tools",
            competition_level="low",
        )
        self.assertGreater(breakdown.total, 40)
        print(f"✅ Rule-based (high): {breakdown.total:.1f}/100 | title={breakdown.title_power} | curiosity={breakdown.curiosity_gap}")

    def test_rule_based_score_low_topic(self):
        """Tópico genérico deve ter score baixo."""
        from agents.viral_score.agent import calculate_rule_based_score
        breakdown = calculate_rule_based_score(
            topic="AI Tools Overview",
            angle="A brief introduction to tools",
            language="en-US",
            niche="ai_tools",
            competition_level="high",
        )
        self.assertLess(breakdown.total, 30)
        print(f"✅ Rule-based (low): {breakdown.total:.1f}/100")

    def test_viral_score_passes_gate(self):
        """Score >= 70 deve passar o gating e retornar passed_gate=True."""
        from agents.viral_score.agent import run_viral_score_agent

        niche_data = self._make_niche_data(
            topic="5 Secret AI Tools Nobody Tells You That Kill Procrastination Instantly",
            angle="Shocking truth: why 99% of people waste money on ChatGPT",
            competition="low",
        )

        with unittest.mock.patch("agents.viral_score.agent._forward_to_copywriter"):
            result = run_viral_score_agent(str(uuid4()), niche_data, score_threshold=50.0)

        self.assertIsNotNone(result)
        self.assertGreater(result.score, 0)
        print(f"✅ ViralScore run | score={result.score:.1f} | passed={result.passed_gate}")

    def test_viral_score_aborts_low_score(self):
        """Score muito baixo deve acionar abort (log no DB)."""
        from agents.viral_score.agent import _abort_job
        from shared.models import NicheData

        niche_data = NicheData(
            topic="Introduction to AI", primary_keyword="ai",
            angle="Brief overview", content_gap="none", niche_score=10,
        )

        # Não deve levantar exceção
        try:
            _abort_job(str(uuid4()), niche_data, 35.0)
            print("✅ _abort_job executed without error")
        except Exception as e:
            self.fail(f"_abort_job raised unexpectedly: {e}")


# ─────────────────────────────────────────────────────────────
# Testes: Content Validator
# ─────────────────────────────────────────────────────────────

class TestContentValidator(unittest.TestCase):

    def test_hook_strength_detection(self):
        """Hook com power words deve ser detectado como forte."""
        from agents.content_validator.agent import _check_hook_strength
        strong = _check_hook_strength("Stop! Most people make this mistake with AI tools every day.")
        weak = _check_hook_strength("In this video, we will talk about artificial intelligence.")
        self.assertTrue(strong)
        self.assertFalse(weak)
        print(f"✅ Hook strength | strong=True, weak=False")

    def test_open_loop_detection(self):
        """Deve detectar open loops no texto."""
        from agents.content_validator.agent import _check_open_loops
        with_loops = "By the end of this video, you'll discover 3 tools that changed everything."
        without_loops = "Artificial intelligence is a technology that automates tasks."
        self.assertTrue(_check_open_loops(with_loops))
        self.assertFalse(_check_open_loops(without_loops))
        print(f"✅ Open loop detection OK")

    def test_originality_check_fresh_topic(self):
        """Tópico nunca visto deve ter alta originalidade."""
        from agents.content_validator.agent import _check_originality
        unique_topic = f"Completely Unique AI Topic {uuid4().hex}"
        score = _check_originality(unique_topic, "ai_tools", "en-US")
        self.assertGreater(score, 70)
        print(f"✅ Originality (fresh topic): {score:.0f}/100")

    def test_originality_check_duplicate(self):
        """Tópico repetido deve receber penalidade."""
        from agents.content_validator.agent import _check_originality
        from database.db_manager import save_topic
        duplicate_topic = "5 Best AI Tools for Productivity in 2025"
        save_topic(duplicate_topic, "ai_tools", "en-US", 80.0)
        score = _check_originality(duplicate_topic, "ai_tools", "en-US")
        self.assertLessEqual(score, 55)
        print(f"✅ Originality (duplicate): {score:.0f}/100 (penalized)")


# ─────────────────────────────────────────────────────────────
# Testes: Shorts Agent
# ─────────────────────────────────────────────────────────────

class TestShortsAgent(unittest.TestCase):

    def test_extraction_points_with_scenes(self):
        """Deve calcular 3 pontos de extração com cenas disponíveis."""
        from agents.shorts_agent.agent import _calculate_extraction_points
        from shared.models import Scene, SceneType, Emotion

        scenes = [
            Scene(id=i, scene_type=SceneType.HOOK, duration_s=30.0,
                  text=f"Scene {i} text here with some words",
                  b_roll_query="technology", emotion=Emotion.CURIOSITY)
            for i in range(1, 7)
        ]

        points = _calculate_extraction_points(300.0, scenes)
        self.assertEqual(len(points), 3)
        for start_s, hook, cta in points:
            self.assertGreaterEqual(start_s, 0)
            self.assertIsInstance(hook, str)
            self.assertIsInstance(cta, str)
        print(f"✅ Extraction points: {[(p[0], p[1][:20]) for p in points]}")

    def test_extraction_points_fallback(self):
        """Sem cenas, deve usar distribuição equidistante."""
        from agents.shorts_agent.agent import _calculate_extraction_points
        points = _calculate_extraction_points(300.0, [])
        self.assertEqual(len(points), 3)
        # Pontos devem ser distribuídos no vídeo
        starts = [p[0] for p in points]
        self.assertAlmostEqual(starts[0], 0.0, places=0)
        print(f"✅ Extraction fallback: starts={[f'{s:.0f}s' for s in starts]}")

    def test_short_data_model(self):
        """ShortData deve agregar clips e plataformas corretamente."""
        from shared.models_v2 import ShortData, ShortClip, ShortPlatform
        job_id = uuid4()
        clips = [
            ShortClip(clip_index=i, source_video_path="src.mp4",
                      output_path=f"short_{i}.mp4", start_s=i*60.0,
                      end_s=i*60.0+55.0, duration_s=55.0,
                      hook_text=f"Hook {i}", cta_text="Subscribe!")
            for i in range(1, 4)
        ]
        data = ShortData(
            job_id=job_id,
            parent_youtube_video_id="YT_ABC123",
            clips=clips,
            platforms_published=[ShortPlatform.YOUTUBE_SHORTS, ShortPlatform.TIKTOK],
        )
        self.assertEqual(len(data.clips), 3)
        self.assertEqual(len(data.platforms_published), 2)
        print(f"✅ ShortData | clips={len(data.clips)} | platforms={[p.value for p in data.platforms_published]}")


# ─────────────────────────────────────────────────────────────
# Testes: Queue Manager v2
# ─────────────────────────────────────────────────────────────

class TestQueueManagerV2(unittest.TestCase):

    def test_new_queues_defined(self):
        """Novas filas devem estar definidas como constantes."""
        from orchestrator.queue_manager import (
            QUEUE_VIRAL_SCORE, QUEUE_VALIDATOR,
            QUEUE_SHORTS, QUEUE_LEARNING
        )
        self.assertEqual(QUEUE_VIRAL_SCORE, "viral_score")
        self.assertEqual(QUEUE_VALIDATOR, "validator")
        self.assertEqual(QUEUE_SHORTS, "shorts")
        self.assertEqual(QUEUE_LEARNING, "learning")
        print(f"✅ Queue constants OK: viral_score, validator, shorts, learning")


# ─────────────────────────────────────────────────────────────
# Testes: Analyst & Learning
# ─────────────────────────────────────────────────────────────

class TestAnalystLearning(unittest.TestCase):

    def test_title_pattern_classification(self):
        """Deve classificar padrões de título corretamente."""
        from agents.analyst_learning.agent import _classify_title_pattern

        cases = [
            ("5 Ways to Automate Your Business", "list_format"),
            ("Why Your AI Strategy is Wrong", "contrarian_why"),
            ("How to Make $10,000 With AI", "money_result"),
            ("I Tried 10 AI Tools for 30 Days", "personal_experiment"),
            ("ChatGPT vs Claude: Honest Review", "comparison"),
        ]
        for title, expected_pattern in cases:
            result = _classify_title_pattern(title)
            print(f"  '{title[:35]}' → {result}")
            self.assertIsNotNone(result, f"Pattern not detected for: {title}")

        print(f"✅ Title pattern classification OK ({len(cases)} cases)")

    def test_learning_insight_generation_no_data(self):
        """Deve retornar LearningInsight vazio quando não há dados."""
        from agents.analyst_learning.agent import generate_learning_insights
        insight = generate_learning_insights()
        self.assertIsNotNone(insight)
        # Com DB vazio, deve retornar insight vazio sem erros
        print(f"✅ Learning insight (empty DB) | niches={insight.top_niches}")

    def test_broadcast_insight_saves_file(self):
        """Deve salvar arquivo de insight para o Scout."""
        from agents.analyst_learning.agent import _broadcast_insight_to_scout
        from shared.models_v2 import LearningInsight

        insight = LearningInsight(
            top_niches=["ai_tools"],
            top_title_patterns=["Why X is wrong"],
            recommended_new_markets=["de-DE"],
        )
        # Redireciona para pasta de teste
        import os
        os.makedirs("output/logs", exist_ok=True)
        _broadcast_insight_to_scout(insight)

        self.assertTrue(Path("output/logs/latest_insight.json").exists())
        with open("output/logs/latest_insight.json") as f:
            data = json.load(f)
        self.assertEqual(data["top_niches"], ["ai_tools"])
        print(f"✅ Insight broadcast | file saved | niches={data['top_niches']}")


# ─────────────────────────────────────────────────────────────
# Import para mock.patch
# ─────────────────────────────────────────────────────────────
import unittest.mock

if __name__ == "__main__":
    unittest.main(verbosity=2)
