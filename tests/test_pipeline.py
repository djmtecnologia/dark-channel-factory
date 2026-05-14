"""Tests for Scout & Miner Agent."""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

os.environ["MOCK_LLM"] = "true"
os.environ["SKIP_UPLOAD"] = "true"
os.environ["DRY_RUN"] = "true"


class TestScoutAgent(unittest.TestCase):

    @patch("agents.scout_miner.youtube_trends.collect_trend_data")
    def test_scout_returns_niche_data(self, mock_trends):
        """Test that Scout Agent returns valid NicheData."""
        mock_trends.return_value = {
            "trending_queries": {"rising": ["AI tools 2025", "ChatGPT automation"], "top": []},
            "competition_analysis": [],
            "seed_keywords": [],
            "target_region": "US",
            "language": "en-US",
        }

        # Mock the queue enqueue
        with patch("agents.scout_miner.agent.get_queue_manager") as mock_qm:
            mock_qm.return_value.enqueue = MagicMock()

            from agents.scout_miner.agent import run_scout_agent
            result = run_scout_agent(str(uuid4()), "ai_tools_us", False)

            self.assertIsNotNone(result)
            self.assertIsNotNone(result.topic)
            self.assertIsNotNone(result.primary_keyword)
            self.assertGreater(result.niche_score, 0)
            print(f"✅ Scout result: {result.topic}")

    def test_trend_data_collection_mock(self):
        """Test trend data collection with no API key."""
        from agents.scout_miner.youtube_trends import collect_trend_data
        data = collect_trend_data("en-US")

        self.assertIn("trending_queries", data)
        self.assertIn("competition_analysis", data)
        print(f"✅ Trend data: {len(data['trending_queries'].get('rising', []))} rising queries")

    def test_affiliate_product_selection(self):
        """Test affiliate product relevance scoring."""
        from agents.copywriter.affiliate_links import get_relevant_products

        products = get_relevant_products(
            ["AI writing", "content creation", "marketing automation"],
            max_products=3,
        )
        self.assertEqual(len(products), 3)
        self.assertTrue(all("product_name" in p for p in products))
        print(f"✅ Top affiliate: {products[0]['product_name']} ({products[0]['commission_percent']}%)")


class TestModels(unittest.TestCase):

    def test_niche_data_serialization(self):
        """Test NicheData Pydantic model."""
        from shared.models import NicheData, Niche
        niche = NicheData(
            topic="5 AI Tools That Will Automate Your Business in 2025",
            primary_keyword="AI tools business",
            secondary_keywords=["automation", "productivity"],
            lsi_keywords=["ChatGPT", "workflow"],
            angle="Contrarian: Real ROI numbers exposed",
            content_gap="No one shows real numbers",
            estimated_cpm_usd=28.5,
            niche_score=87,
        )
        serialized = niche.model_dump(mode="json")
        deserialized = NicheData(**serialized)
        self.assertEqual(niche.topic, deserialized.topic)
        print(f"✅ NicheData serialization OK | job_id={niche.job_id}")

    def test_pipeline_job_status_update(self):
        """Test PipelineJob status transitions."""
        from shared.models import PipelineJob, VideoStatus
        job = PipelineJob()
        self.assertEqual(job.status, VideoStatus.PENDING)
        job.update_status(VideoStatus.SCRIPTING)
        self.assertEqual(job.status, VideoStatus.SCRIPTING)
        print(f"✅ PipelineJob status PENDING → SCRIPTING OK")


class TestTTSEngine(unittest.TestCase):

    def test_text_cleaning(self):
        """Test TTS text cleaner removes markdown."""
        from agents.video_editor.tts_engine import _clean_text_for_tts
        dirty = "**Hello** _world_! Check https://example.com 🚀"
        clean = _clean_text_for_tts(dirty)
        self.assertNotIn("**", clean)
        self.assertNotIn("https://", clean)
        self.assertGreater(len(clean), 0)
        print(f"✅ TTS clean: '{clean}'")


class TestSubtitleGenerator(unittest.TestCase):

    def test_generates_srt_file(self):
        """Test SRT subtitle generation."""
        import tempfile
        from agents.video_editor.subtitle_generator import generate_hormozi_subtitles

        scenes = [
            {"id": 1, "text": "This is the first scene with some words for testing purposes."},
            {"id": 2, "text": "And this is the second scene also for testing."},
        ]
        audio_durations = [5.0, 4.0]

        with tempfile.NamedTemporaryFile(suffix=".srt", delete=False) as f:
            srt_path = f.name

        result = generate_hormozi_subtitles(scenes, audio_durations, srt_path)

        with open(result, "r") as f:
            content = f.read()

        self.assertIn("-->", content)
        print(f"✅ SRT generated: {len(content.splitlines())} lines")


class TestAffiliateLinks(unittest.TestCase):

    def test_pinned_comment_generation(self):
        """Test pinned comment builder."""
        from agents.copywriter.affiliate_links import build_pinned_comment, AI_TOOLS_PRODUCTS
        products = AI_TOOLS_PRODUCTS[:2]
        comment = build_pinned_comment(products)
        self.assertIn("affiliate", comment.lower())
        self.assertGreater(len(comment), 50)
        print(f"✅ Pinned comment: {len(comment)} chars")


class TestStorageUtils(unittest.TestCase):

    def test_directory_creation(self):
        """Test job directory creation."""
        from shared.storage import job_audio_path, job_video_path, job_thumbnail_path
        import tempfile
        import os

        os.environ["AUDIO_DIR"] = tempfile.mkdtemp()
        os.environ["VIDEOS_DIR"] = tempfile.mkdtemp()
        os.environ["THUMBNAILS_DIR"] = tempfile.mkdtemp()

        job_id = uuid4()
        audio_dir = job_audio_path(job_id)
        video_dir = job_video_path(job_id)
        thumb_dir = job_thumbnail_path(job_id)

        self.assertTrue(audio_dir.exists())
        self.assertTrue(video_dir.exists())
        self.assertTrue(thumb_dir.exists())
        print(f"✅ Storage directories created for job {job_id}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
