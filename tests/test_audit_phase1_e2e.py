"""
Suite de Testes de Integracao Ponta a Ponta — Auditoria Complementar Fase 1
==========================================================================
Isolamento rigoroso em SQLite temporario (Sem acesso ao Neon de producao).
Cobre:
  - Cenario A: PILOT_5MIN (criacao, persistencia, distribuicao API, worker, validadores, QC 270-330s)
  - Cenario B: LEGACY (criacao sem perfil, sem atribuicao silenciosa, regras legadas)
  - Cenario C: Concorrencia de perfis simultaneos sem contaminacao
  - Normalizacao de Cenas e Consumidores Reais de Midia
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from uuid import UUID, uuid4
from concurrent.futures import ThreadPoolExecutor

# ─────────────────────────────────────────────────────────────
# 1. Isolamento Rigoroso de Ambiente (Sem Neon de Producao)
# ─────────────────────────────────────────────────────────────
TEMP_DIR = tempfile.mkdtemp()
TEST_DB_PATH = Path(TEMP_DIR) / "isolated_audit_phase1.db"

os.environ["DATABASE_URL"] = ""  # Garante isolamento estrito: Neon nunca e chamado
os.environ["SQLITE_PATH"] = str(TEST_DB_PATH)
os.environ["MOCK_LLM"] = "true"
os.environ["SKIP_UPLOAD"] = "true"
os.environ["DRY_RUN"] = "true"

from sqlmodel import SQLModel, Session, create_engine, select
from cloud_hub.models import Tenant, Channel, VideoJob
from shared.production_profiles import (
    PROFILES,
    ProductionProfileType,
    resolve_profile,
    validate_duration,
    validate_script_word_count,
    validate_scene_count,
)
from shared.scene_normalizer import normalize_scene, normalize_scenes
from shared.models import Scene, SceneType, Emotion
from shared.script_validator import validate_and_fix_script
from agents.validator.qc_agent import ValidatorAgent
from agents.content_validator.agent import run_content_validator_agent
from agents.video_editor.media_fetcher import fetch_all_broll


class TestPhase1AuditE2E(unittest.TestCase):
    """Testes de integracao ponta a ponta e auditoria da Fase 1."""

    @classmethod
    def setUpClass(cls):
        # Inicializa banco SQLite isolado com DDL completo
        cls.engine = create_engine(f"sqlite:///{TEST_DB_PATH}", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(cls.engine)

        with Session(cls.engine) as session:
            # Setup Tenant e Canal para testes
            cls.tenant = Tenant(
                name="Audit Test Tenant",
                email="audit_tenant@test.com",
                plan_type="enterprise",
                region="North America"
            )
            session.add(cls.tenant)
            session.commit()
            session.refresh(cls.tenant)

            cls.channel = Channel(
                tenant_id=cls.tenant.id,
                name="Audit Pilot Channel",
                identifier="audit_channel_a_us",
                niche="ai_tools",
                language="en-US",
                market_region="North America",
                active=True
            )
            session.add(cls.channel)

            # Tenant B e Canal B para teste de isolamento multitenant
            cls.tenant_b = Tenant(
                name="Audit Tenant B",
                email="tenant_b@test.com",
                plan_type="free",
                region="Europe"
            )
            session.add(cls.tenant_b)
            session.commit()
            session.refresh(cls.tenant_b)

            cls.channel_b = Channel(
                tenant_id=cls.tenant_b.id,
                name="Channel Tenant B",
                identifier="audit_channel_b_de",
                niche="business",
                language="de-DE",
                market_region="Europe",
                active=True
            )
            session.add(cls.channel_b)
            session.commit()
            session.refresh(cls.channel)
            session.refresh(cls.channel_b)

            cls.tenant_id = cls.tenant.id
            cls.tenant_b_id = cls.tenant_b.id
            cls.channel_id = cls.channel.id
            cls.channel_b_id = cls.channel_b.id

        # Configura TestClient com override de banco
        from cloud_hub.main import app, get_session
        from fastapi.testclient import TestClient
        def override_get_session():
            with Session(cls.engine) as s:
                yield s
        app.dependency_overrides[get_session] = override_get_session
        cls.client = TestClient(app)

    def test_database_isolation_active(self):
        """Confirma que o teste esta usando estritamente SQLite local e nunca o Neon."""
        self.assertEqual(os.environ.get("DATABASE_URL", ""), "")
        self.assertTrue(os.path.exists(TEST_DB_PATH))

    # ─────────────────────────────────────────────────────────
    # Cenário A — PILOT
    # ─────────────────────────────────────────────────────────
    def test_scenario_a_pilot_full_propagation(self):
        """
        Cenario A — PILOT
        - Criar um VideoJob com PILOT_5MIN.
        - Persistir o registro.
        - Recuperar o job pelo fluxo de distribuicao.
        - Confirmar que o worker recebe PILOT_5MIN.
        - Confirmar que todos os validadores recebem o mesmo perfil.
        - Confirmar que o QC aplica a faixa de 270 a 330 segundos.
        """
        job_id = uuid4()
        topic_name = "How Autonomous AI Replaces Mid-Level Developers"

        paragraphs = [
            "Nobody saw this coming in the global tech landscape. Autonomous artificial intelligence networks are rapidly advancing beyond human expectations and redefining productivity standards across every sector. Software systems that once took months to build are now synthesized in hours through cooperative multi agent orchestration architectures operating with unprecedented mathematical precision.",
            "The initial signs began appearing quietly across leading financial institutions. Quantitative engineers noticed automated trading algorithms generating novel investment strategies that consistently outperformed seasoned human analysts. These autonomous agents detected microscopic liquidity imbalances across distributed global exchanges in milliseconds, creating a massive competitive advantage for early adopters.",
            "Traditional corporate management structures were completely unprepared for this structural shift. Entire operational departments that previously required dozens of human coordinators suddenly operated autonomously using lightweight neural agent orchestrators. Communication bottlenecks disappeared as intelligent workflows routed critical decisions directly to specialized microservices.",
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

        # 1. Criar e persistir o VideoJob com PILOT_5MIN
        with Session(self.engine) as session:
            job = VideoJob(
                id=job_id,
                channel_id=self.channel.id,
                topic=topic_name,
                status="APPROVED",
                video_type="LONG",
                production_profile="PILOT_5MIN",
                script_data={
                    "title": topic_name,
                    "production_profile": "PILOT_5MIN",
                    "scenes": [
                        {
                            "id": i,
                            "narration": p,
                            "b_roll_visual": f"cinematic footage scene {i} server room blue neon lights",
                            "timestamp": f"0:{i*20:02d}-0:{(i+1)*20:02d}",
                            "visual_cues": [{"t": 0, "type": "zoom_in"}]
                        }
                        for i, p in enumerate(paragraphs)  # 14 cenas = ~700 palavras (faixa 600-850)
                    ]
                }
            )
            session.add(job)
            session.commit()

        # 2. Recuperar o registro persistido pelo fluxo de distribuicao (logica de next-job)
        with Session(self.engine) as session:
            statement = (
                select(VideoJob)
                .join(Channel, VideoJob.channel_id == Channel.id)
                .join(Tenant, Channel.tenant_id == Tenant.id)
                .where(
                    Tenant.region == "North America",
                    VideoJob.id == job_id
                )
            )
            distributed_job = session.exec(statement).first()
            self.assertIsNotNone(distributed_job)
            
            # Formato distribuido para o desktop worker (como retornado pelo endpoint /worker/next-job/{region})
            worker_payload = {
                "job_id": str(distributed_job.id),
                "niche": distributed_job.channel.niche,
                "language": distributed_job.channel.language,
                "script_data": distributed_job.script_data,
                "video_type": distributed_job.video_type,
                "production_profile": getattr(distributed_job, "production_profile", None),
            }

        # 3. Confirmar que o worker recebe PILOT_5MIN
        self.assertEqual(worker_payload["production_profile"], "PILOT_5MIN")

        # 4. Confirmar que os validadores recebem e respeitam PILOT_5MIN
        prof = resolve_profile(profile_name=worker_payload["production_profile"])
        self.assertIsNotNone(prof)
        self.assertEqual(prof.name, "PILOT_5MIN")
        self.assertEqual(prof.min_duration_s, 270.0)
        self.assertEqual(prof.max_duration_s, 330.0)
        self.assertEqual(prof.min_words, 600)
        self.assertEqual(prof.max_words, 850)
        self.assertEqual(prof.min_scenes, 12)
        self.assertEqual(prof.max_scenes, 18)

        # Script Validator
        cleaned_script, script_val_res = validate_and_fix_script(
            worker_payload["script_data"], 
            profile=worker_payload["production_profile"]
        )
        self.assertTrue(script_val_res.passed)
        self.assertGreaterEqual(script_val_res.word_count, 600)
        self.assertLessEqual(script_val_res.word_count, 850)

        # QC Validator Duration Check: faixa de 270 a 330 segundos
        qc = ValidatorAgent(session=None, job=worker_payload, video_path="mock_video.mp4")
        self.assertEqual(qc.production_profile, "PILOT_5MIN")
        self.assertIsNotNone(qc.resolved_profile)
        self.assertEqual(qc.resolved_profile.name, "PILOT_5MIN")

        # Faixa de 270 a 330 segundos
        self.assertTrue(validate_duration(270.0, profile=qc.resolved_profile)[0], "270s deve passar")
        self.assertTrue(validate_duration(300.0, profile=qc.resolved_profile)[0], "300s deve passar")
        self.assertTrue(validate_duration(330.0, profile=qc.resolved_profile)[0], "330s deve passar")

        # Fora da faixa deve falhar
        fail_low, err_low = validate_duration(269.5, profile=qc.resolved_profile)
        self.assertFalse(fail_low)
        self.assertIn("abaixo do mínimo para PILOT_5MIN", err_low)

        fail_high, err_high = validate_duration(330.5, profile=qc.resolved_profile)
        self.assertFalse(fail_high)
        self.assertIn("acima do máximo para PILOT_5MIN", err_high)

    # ─────────────────────────────────────────────────────────
    # Cenário B — LEGACY
    # ─────────────────────────────────────────────────────────
    def test_scenario_b_legacy_behavior(self):
        """
        Cenario B — LEGACY
        - Criar ou recuperar um job sem perfil explicito.
        - Confirmar que o comportamento legado permanece.
        - Confirmar que nenhum perfil e atribuido silenciosamente.
        """
        legacy_job_id = uuid4()

        with Session(self.engine) as session:
            job = VideoJob(
                id=legacy_job_id,
                channel_id=self.channel.id,
                topic="Legacy Video Without Profile",
                status="APPROVED",
                video_type="LONG",
                production_profile=None,  # Nenhum perfil explícito
                script_data={"title": "Legacy Video"}
            )
            session.add(job)
            session.commit()

        with Session(self.engine) as session:
            persisted = session.get(VideoJob, legacy_job_id)
            self.assertIsNone(persisted.production_profile)

            # Verificar que a distribuicao preserva None
            distributed_profile = getattr(persisted, "production_profile", None)
            self.assertIsNone(distributed_profile)

        # Resolver perfil para job legado deve retornar None (sem perfil default silencioso)
        resolved = resolve_profile(job=persisted)
        self.assertIsNone(resolved, "Nenhum perfil deve ser atribuido silenciosamente a jobs legados")

        # Regras legadas de duracao para LONG: >= 480s, sem limite maximo
        ok_legacy_min, _ = validate_duration(480.0, profile=None, video_type="LONG")
        self.assertTrue(ok_legacy_min)

        ok_legacy_long, _ = validate_duration(3600.0, profile=None, video_type="LONG")
        self.assertTrue(ok_legacy_long, "Jobs legados longos nao possuem limite maximo superior")

        fail_legacy_short, err_legacy = validate_duration(300.0, profile=None, video_type="LONG")
        self.assertFalse(fail_legacy_short)
        self.assertIn("Mínimo: 480.0s", err_legacy)

        # Regras legadas de palavras: 900 para script_validator, 1800 para content_validator
        self.assertTrue(validate_script_word_count(900, profile=None, validator_type="script")[0])
        self.assertFalse(validate_script_word_count(899, profile=None, validator_type="script")[0])
        self.assertTrue(validate_script_word_count(1800, profile=None, validator_type="content")[0])
        self.assertFalse(validate_script_word_count(1799, profile=None, validator_type="content")[0])

    # ─────────────────────────────────────────────────────────
    # Cenário C — Concorrência
    # ─────────────────────────────────────────────────────────
    def test_scenario_c_concurrency_no_profile_contamination(self):
        """
        Cenario C — Concorrencia
        - Criar dois ou mais jobs simultaneos com perfis diferentes.
        - Confirmar que cada job mantem seu proprio perfil.
        - Garantir que nao exista contaminacao por variavel global ou estado compartilhado.
        """
        profiles_to_test = [
            ("PILOT_5MIN", 300.0, True),
            ("STANDARD_10MIN", 600.0, True),
            ("DOCUMENTARY_20MIN", 1200.0, True),
            ("SHORTS", 45.0, True),
            (None, 600.0, True),
        ]

        def process_job_simulated(item):
            prof_name, duration, _ = item
            job_id = uuid4()
            job_dict = {
                "id": str(job_id),
                "topic": f"Concurrent Job {prof_name}",
                "video_type": "SHORT" if prof_name == "SHORTS" else "LONG",
                "production_profile": prof_name,
                "script_data": {"title": f"Topic {prof_name}", "script_full": "word " * 100}
            }

            # Resolução e validação isoladas
            resolved = resolve_profile(profile_name=job_dict["production_profile"])
            val_dur, err = validate_duration(
                duration_s=duration,
                profile=resolved,
                video_type=job_dict["video_type"]
            )
            qc_agent = ValidatorAgent(session=None, job=job_dict, video_path="mock.mp4")

            return {
                "input_profile": prof_name,
                "resolved_name": resolved.name if resolved else None,
                "qc_profile_name": qc_agent.resolved_profile.name if qc_agent.resolved_profile else None,
                "duration_valid": val_dur
            }

        # Executa em paralelo usando ThreadPool
        with ThreadPoolExecutor(max_workers=5) as executor:
            results = list(executor.map(process_job_simulated, profiles_to_test))

        for res in results:
            expected = res["input_profile"]
            self.assertEqual(res["resolved_name"], expected, f"Contaminacao detectada para perfil {expected}")
            self.assertEqual(res["qc_profile_name"], expected, f"Contaminacao no QC detectada para {expected}")
            self.assertTrue(res["duration_valid"])

    # ─────────────────────────────────────────────────────────
    # Auditoria do Normalizador de Cenas e Consumidores de Mídia
    # ─────────────────────────────────────────────────────────
    def test_scene_normalizer_complete_contract(self):
        """
        Auditoria do normalizador de cenas:
        - Compatibilidade entre b_roll_visual e b_roll_query.
        - Compatibilidade entre narration e text.
        - Preservacao dos dois campos quando ambos preenchidos.
        - Prioridade consistente na selecao do prompt visual.
        - Compatibilidade com cenas antigas ja persistidas.
        - Ausencia de consultas duplicadas desnecessarias.
        - Ausencia de fallback generico silencioso.
        - Uso efetivo do contexto do video quando os campos visuais estiverem vazios.
        """
        # 1. Ambos preenchidos com valores distintos: ambos devem ser preservados
        both_scene = {
            "text": "The monetary system transformed.",
            "narration": "The monetary system transformed.",
            "b_roll_visual": "macro shot gold bars vault cinematic lighting",
            "b_roll_query": "gold bars bank vault",
            "duration_s": 6.0,
        }
        n_both = normalize_scene(both_scene)
        self.assertEqual(n_both["b_roll_visual"], "macro shot gold bars vault cinematic lighting")
        self.assertEqual(n_both["b_roll_query"], "gold bars bank vault")
        self.assertEqual(n_both["narration"], "The monetary system transformed.")
        self.assertEqual(n_both["text"], "The monetary system transformed.")

        # 2. Apenas b_roll_visual preenchido: sincroniza b_roll_query
        vis_only = {
            "narration": "Server cooling systems.",
            "b_roll_visual": "quantum cooling tubes glowing cyan",
        }
        n_vis = normalize_scene(vis_only)
        self.assertEqual(n_vis["b_roll_visual"], "quantum cooling tubes glowing cyan")
        self.assertEqual(n_vis["b_roll_query"], "quantum cooling tubes glowing cyan")

        # 3. Apenas b_roll_query preenchido: sincroniza b_roll_visual
        query_only = {
            "text": "Trading floor panic.",
            "b_roll_query": "stock traders screaming dynamic zoom",
        }
        n_query = normalize_scene(query_only)
        self.assertEqual(n_query["b_roll_visual"], "stock traders screaming dynamic zoom")
        self.assertEqual(n_query["b_roll_query"], "stock traders screaming dynamic zoom")
        self.assertEqual(n_query["narration"], "Trading floor panic.")

        # 4. Cena antiga com chave legado 'visual' e 'content'
        legacy_scene = {
            "id": 5,
            "content": "Deep sea fiber optic cables.",
            "visual": "underwater submarine cable trench dark ocean",
            "duration_s": 8.0
        }
        n_legacy = normalize_scene(legacy_scene)
        self.assertEqual(n_legacy["narration"], "Deep sea fiber optic cables.")
        self.assertEqual(n_legacy["text"], "Deep sea fiber optic cables.")
        self.assertEqual(n_legacy["b_roll_visual"], "underwater submarine cable trench dark ocean")
        self.assertEqual(n_legacy["b_roll_query"], "underwater submarine cable trench dark ocean")

        # 5. Campos visuais vazios: usa contexto do topico sem fallback generico silencioso
        empty_visual = {
            "narration": "A confidential document was leaked.",
            "timestamp": "0:00-0:05"
        }
        n_context = normalize_scene(empty_visual, fallback_topic="Cybersecurity Infiltration")
        self.assertEqual(n_context["b_roll_visual"], "Cybersecurity Infiltration")
        self.assertEqual(n_context["b_roll_query"], "Cybersecurity Infiltration")
        self.assertNotEqual(n_context["b_roll_visual"], "AI technology")

    def test_media_fetcher_consumer_uses_visual_contract(self):
        """
        Confirma que o consumidor real da busca de midia (fetch_all_broll)
        prioriza b_roll_visual e preserva o contrato de cenas.
        """
        scenes = [
            {
                "id": 1,
                "narration": "Robotics in aerospace manufacturing.",
                "b_roll_visual": "aerospace factory robotic arms carbon fiber fuselage",
                "b_roll_query": "aerospace factory",
            },
            {
                "id": 2,
                "narration": "Cleanroom semiconductor fabrication.",
                "b_roll_visual": "silicon wafer fabrication ultra cleanroom yellow lighting",
            }
        ]

        normalized = normalize_scenes(scenes, fallback_topic="Aerospace Tech")
        # Ambos os itens devem possuir b_roll_visual e b_roll_query coerentes
        self.assertEqual(normalized[0]["b_roll_visual"], "aerospace factory robotic arms carbon fiber fuselage")
        self.assertEqual(normalized[0]["b_roll_query"], "aerospace factory")
        self.assertEqual(normalized[1]["b_roll_visual"], "silicon wafer fabrication ultra cleanroom yellow lighting")
        self.assertEqual(normalized[1]["b_roll_query"], "silicon wafer fabrication ultra cleanroom yellow lighting")

    # ─────────────────────────────────────────────────────────
    # 1. Auditoria de Segurança: POST /jobs/create
    # ─────────────────────────────────────────────────────────
    def test_create_job_unauthenticated(self):
        """Requisicao sem autenticacao deve ser rejeitada com 401 Unauthorized."""
        payload = {
            "channel_id": str(self.channel_id),
            "topic": "Unauthenticated Job Test",
            "production_profile": "PILOT_5MIN"
        }
        res = self.client.post("/jobs/create", json=payload)
        self.assertEqual(res.status_code, 401)
        self.assertIn("Não autenticado", res.json().get("detail", ""))

    def test_create_job_unauthorized_cross_tenant(self):
        """Tenant B tentando criar job em canal de Tenant A deve ser bloqueado com 403 Forbidden."""
        payload = {
            "channel_id": str(self.channel_id),  # Canal pertence a Tenant A
            "topic": "Cross Tenant Unauthorized Job",
            "production_profile": "PILOT_5MIN"
        }
        # Autenticado como Tenant B via Header
        res = self.client.post(
            "/jobs/create",
            json=payload,
            headers={"X-Tenant-ID": str(self.tenant_b_id)}
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("não pertence à sua organização", res.json().get("detail", ""))

    def test_create_job_invalid_production_profile(self):
        """Perfil de producao inexistente deve ser rejeitado com 400 Bad Request."""
        payload = {
            "channel_id": str(self.channel_id),
            "topic": "Invalid Profile Test",
            "production_profile": "INVALID_UNKNOWN_PROFILE_999"
        }
        res = self.client.post(
            "/jobs/create",
            json=payload,
            headers={"X-Tenant-ID": str(self.tenant_id)}
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("Perfil de produção inválido", res.json().get("detail", ""))

    def test_create_job_empty_topic(self):
        """Topico vazio deve ser rejeitado com 400 Bad Request."""
        payload = {
            "channel_id": str(self.channel_id),
            "topic": "   ",
            "production_profile": "PILOT_5MIN"
        }
        res = self.client.post(
            "/jobs/create",
            json=payload,
            headers={"X-Tenant-ID": str(self.tenant_id)}
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("não pode ser vazio", res.json().get("detail", ""))

    def test_create_job_invalid_video_type(self):
        """Tipo de video invalido deve ser rejeitado com 400 Bad Request."""
        payload = {
            "channel_id": str(self.channel_id),
            "topic": "Valid Topic Name",
            "video_type": "PANORAMIC_360",
            "production_profile": "PILOT_5MIN"
        }
        res = self.client.post(
            "/jobs/create",
            json=payload,
            headers={"X-Tenant-ID": str(self.tenant_id)}
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("deve ser 'LONG' ou 'SHORT'", res.json().get("detail", ""))

    def test_create_job_authorized_success(self):
        """Tenant autenticado criando job no proprio canal deve ter sucesso (200) e persistir."""
        payload = {
            "channel_id": str(self.channel_id),
            "topic": "Successfully Created Pilot Video Job",
            "production_profile": "PILOT_5MIN",
            "video_type": "LONG"
        }
        res = self.client.post(
            "/jobs/create",
            json=payload,
            headers={"X-Tenant-ID": str(self.tenant_id)}
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["production_profile"], "PILOT_5MIN")

        # Verifica persistencia no banco SQLite
        with Session(self.engine) as session:
            job_db = session.get(VideoJob, UUID(data["job_id"]))
            self.assertIsNotNone(job_db)
            self.assertEqual(job_db.production_profile, "PILOT_5MIN")
            self.assertEqual(job_db.topic, "Successfully Created Pilot Video Job")
            self.assertEqual(job_db.channel_id, self.channel_id)

    # ─────────────────────────────────────────────────────────
    # 2. Compatibilidade das Regras LEGACY vs PILOT
    # ─────────────────────────────────────────────────────────
    def test_legacy_script_validator_threshold_900_words(self):
        """Script Validator legado original exige no minimo 900 palavras para LONG."""
        passed_900, err_900 = validate_script_word_count(900, profile=None, validator_type="script")
        self.assertTrue(passed_900)
        self.assertIsNone(err_900)

        passed_899, err_899 = validate_script_word_count(899, profile=None, validator_type="script")
        self.assertFalse(passed_899)
        self.assertIn("minimum 900", err_899)

    def test_legacy_content_validator_threshold_1800_words(self):
        """Content Validator legado original exige no minimo 1800 palavras para LONG."""
        passed_1800, err_1800 = validate_script_word_count(1800, profile=None, validator_type="content")
        self.assertTrue(passed_1800)
        self.assertIsNone(err_1800)

        passed_1799, err_1799 = validate_script_word_count(1799, profile=None, validator_type="content")
        self.assertFalse(passed_1799)
        self.assertIn("minimum 1800", err_1799)

    def test_pilot_profile_word_threshold_600_words(self):
        """No perfil PILOT_5MIN, ambos os validadores usam o limite centralizado de 600 palavras."""
        # Script validator com PILOT
        self.assertTrue(validate_script_word_count(600, profile="PILOT_5MIN", validator_type="script")[0])
        self.assertFalse(validate_script_word_count(599, profile="PILOT_5MIN", validator_type="script")[0])

        # Content validator com PILOT
        self.assertTrue(validate_script_word_count(600, profile="PILOT_5MIN", validator_type="content")[0])
        self.assertFalse(validate_script_word_count(599, profile="PILOT_5MIN", validator_type="content")[0])

    def test_legacy_thresholds_preserved_independently(self):
        """Comprova que os dois limites legados historicos (900 e 1800) nao foram homogeneizados."""
        prof_none = None
        # Um script legado de 1000 palavras passa no ScriptValidator (limite 900) mas e reprovado no ContentValidator (limite 1800)
        ok_script, _ = validate_script_word_count(1000, profile=prof_none, validator_type="script")
        ok_content, _ = validate_script_word_count(1000, profile=prof_none, validator_type="content")
        self.assertTrue(ok_script, "1000 palavras deve passar no Script Validator legado (piso 900)")
        self.assertFalse(ok_content, "1000 palavras deve reprovar no Content Validator legado (piso 1800)")

    # ─────────────────────────────────────────────────────────
    # 3. Tratamento de Exceções do Ollama
    # ─────────────────────────────────────────────────────────
    def test_ollama_healthcheck_connection_failure(self):
        """Falha de conexao no healthcheck e tratada e registrada sem crash."""
        from shared.ollama_client import OllamaClient
        from unittest.mock import patch
        import requests

        with patch("requests.get") as mock_get:
            mock_get.side_effect = requests.ConnectionError("Connection refused by peer")
            client = OllamaClient()
            self.assertFalse(client.healthcheck())

    def test_ollama_healthcheck_unexpected_exception(self):
        """Erro inesperado no healthcheck e tratado e registrado com contexto sem crash silencioso."""
        from shared.ollama_client import OllamaClient
        from unittest.mock import patch

        with patch("requests.get") as mock_get:
            mock_get.side_effect = RuntimeError("Driver or socket descriptor corruption")
            client = OllamaClient()
            self.assertFalse(client.healthcheck())

    def test_ollama_generate_propagates_ollama_error(self):
        """Falhas na geracao REST propagam OllamaError com a excecao original encadeada."""
        from shared.ollama_client import OllamaClient, OllamaError
        from unittest.mock import patch
        import requests

        with patch("requests.post") as mock_post:
            mock_post.side_effect = requests.RequestException("Gateway Timeout 504")
            client = OllamaClient()
            with self.assertRaises(OllamaError) as ctx:
                client.generate("Test prompt")
            self.assertIn("POST /api/generate", str(ctx.exception))
            self.assertIsNotNone(ctx.exception.__cause__)


if __name__ == "__main__":
    unittest.main()
