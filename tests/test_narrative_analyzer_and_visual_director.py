"""
Testes Automatizados do NarrativeAnalyzer e VisualDirector — Sprint 2.2
========================================================================
Valida a conformidade da interpretação semântica, direção visual e integração
com o Contrato Canônico V2 após as correções da auditoria independente.

Cobre os 12 cenários canônicos originais + 10 cenários específicos das correções:
1. Roteiro documental normal.
2. Roteiro com cenas de forte tensão.
3. Roteiro histórico.
4. Roteiro investigativo.
5. Roteiro com informações insuficientes ou vazias.
6. Resposta inválida do LLM (schema incorreto).
7. JSON malformado do LLM.
8. Timeout do provedor e falhas de rede.
9. Compatibilidade com o Contrato Canônico V2.
10. Preservação integral do roteiro original (imutabilidade textual).
11. Determinismo das validações Python e normalização de queries.
12. Ausência de mutação dos objetos de entrada (cópia defensiva).
13. Preservação exata da narração (espaços, tabs, quebras de linha, Unicode, acentos).
14. Propagação real do timeout e encapsulamento com causa preservada.
15. Rejeição explícita de Bíblias Visuais inválidas (sem fallback silencioso).
16. Validação estrita de b_roll_query (2 a 5 palavras significativas).
17. Validação estrita de narrative_progression (3 a 6 etapas, sem vazias ou duplicadas).
18. Preservação de dados contextuais na conversão para Scene V2 (duração, timestamp, tipo, etc.).
19. Classificação rigorosa de EvidenceType e separação epistêmica.
20. Validação do limite de até 3 palavras em on_screen_text.
21. Rejeição rigorosa de divergências de identificadores, contagem e ordem de cenas.
22. Sanitização universal de credenciais e tokens em mensagens e logs.
"""
from __future__ import annotations

import copy
import json
import unittest
from typing import Any, Dict, List
from unittest.mock import MagicMock, create_autospec
from shared.llm_client import LLMClient

import pytest
from pydantic import ValidationError

from agents.video_director import (
    EvidenceType,
    NarrativeAnalysisError,
    NarrativeAnalyzer,
    NarrativeAnalysisResult,
    SceneNarrativeInterpretation,
    SceneVisualDirection,
    VisualBible,
    VisualDirectionError,
    VisualDirectionResult,
    VisualDirector,
    VisualImpactPotential,
    VisualLanguage,
    sanitize_secret,
)
from shared.models import (
    CameraFraming,
    CameraMovement,
    Emotion,
    NarrativePurpose,
    Scene,
    SceneType,
    ScriptData,
    VisualProductionType,
)
from shared.scene_normalizer import normalize_scene_v2


class MockLLMResponse:
    """Wrapper de resposta mockada compatível com shared.llm_client.LLMResponse."""
    def __init__(self, text: str):
        self.text = text


class TestNarrativeAnalyzerAndVisualDirector(unittest.TestCase):
    """Suíte completa de validação para o Sprint 2.2."""

    def setUp(self):
        # Fixture de roteiro documental padrão
        self.sample_doc_script = {
            "title": "A Conquista da DeepWeb",
            "scenes": [
                {
                    "id": 1,
                    "narration": "Nos recantos mais obscuros da internet, uma rede paralela opera além do alcance dos motores de busca convencionais.",
                },
                {
                    "id": 2,
                    "narration": "Em 2013, o FBI executou a maior operação cibernética da década para desmantelar o Silk Road.",
                },
                {
                    "id": 3,
                    "narration": "O impacto dessa apreensão redefiniu para sempre a arquitetura da criptografia e a privacidade digital moderna.",
                },
            ]
        }

    def _create_sample_narrative_analysis(self, scenes=None):
        if scenes is None:
            scenes = [
                SceneNarrativeInterpretation(
                    scene_id=1,
                    original_narration="A inteligência artificial transformou a produção cinematográfica.",
                    scene_intent="Apresentar a transformação tecnológica",
                    narrative_purpose=NarrativePurpose.CONTEXT,
                    predominant_emotion=Emotion.CURIOSITY,
                    emotional_intensity=0.6,
                    narrative_tension=0.5,
                    dramatic_importance=0.7,
                    factual_assertions=["A IA transformou o cinema"],
                    narrative_inferences=["Novos fluxos surgirão na indústria audiovisual"],
                    visual_proof_requirements=[],
                    forbidden_fabrications=[],
                    required_visual_elements=["Câmeras virtuais"],
                    evidence_needed=[EvidenceType.DATA_METRIC],
                    visual_impact_potential=VisualImpactPotential.HIGH,
                )
            ]
        return NarrativeAnalysisResult(
            central_theme="Cinema com IA",
            historical_or_factual_context="2026",
            narrative_objective="Explorar cinema generativo",
            narrative_progression=[
                "Introdução e contextualização tecnológica",
                "Desenvolvimento das ferramentas generativas",
                "Impacto estrutural no ecossistema cinematográfico"
            ],
            dominant_tone="cinematic",
            scene_analyses=scenes,
            raw_word_count=sum(len(s.original_narration.split()) for s in scenes),
            total_scenes=len(scenes),
        )

    # ─────────────────────────────────────────────────────────────
    # 1. Roteiro Documental Normal
    # ─────────────────────────────────────────────────────────────
    def test_01_documentary_normal_script(self):
        """1. Roteiro documental normal deve produzir interpretação narrativa e direção visual completas."""
        analyzer_mock = create_autospec(LLMClient, instance=True)
        analyzer_mock.complete.return_value = MockLLMResponse(json.dumps({
            "central_theme": "Origem e impacto do Silk Road e da privacidade digital",
            "historical_or_factual_context": "2013, Operação conjunta do FBI contra servidores do Silk Road",
            "narrative_objective": "Revelar como a apreensão redefiniu a segurança digital global",
            "narrative_progression": [
                "Introdução ao submundo da rede paralela",
                "Operação tática de desmantelamento pelo FBI",
                "Transformação da privacidade e criptografia moderna"
            ],
            "dominant_tone": "documentary_investigative",
            "scene_analyses": [
                {
                    "scene_id": 1,
                    "original_narration": self.sample_doc_script["scenes"][0]["narration"],
                    "scene_intent": "Estabelecer o mistério e a escala da rede invisível",
                    "narrative_purpose": "hook",
                    "predominant_emotion": "curiosity",
                    "emotional_intensity": 0.7,
                    "narrative_tension": 0.6,
                    "dramatic_importance": 0.8,
                    "factual_assertions": ["Existe uma rede paralela que não é indexada por motores de busca convencionais"],
                    "narrative_inferences": ["A navegação convencional é apenas a superfície de um ecossistema mais denso"],
                    "visual_proof_requirements": ["Diagramas de rede, visualizações de nós onion, servidores"],
                    "forbidden_fabrications": ["Não inventar código de ataque fictício no estilo Hollywood"],
                    "required_visual_elements": ["Servidores em racks escuros, fluxos de dados cifrados"],
                    "evidence_needed": ["map", "data_metric"],
                    "visual_impact_potential": "high"
                },
                {
                    "scene_id": 2,
                    "original_narration": self.sample_doc_script["scenes"][1]["narration"],
                    "scene_intent": "Narrar a ofensiva policial e o colapso do mercado clandestino",
                    "narrative_purpose": "historical_evidence",
                    "predominant_emotion": "shock",
                    "emotional_intensity": 0.85,
                    "narrative_tension": 0.8,
                    "dramatic_importance": 0.9,
                    "factual_assertions": ["O FBI derrubou o Silk Road em 2013 na maior operação cibernética da década"],
                    "narrative_inferences": ["A operação exigiu cooperação de inteligência internacional"],
                    "visual_proof_requirements": ["Comunicado de imprensa do FBI de 2013, foto do mandado judicial"],
                    "forbidden_fabrications": ["Não inventar nomes falsos de agentes ou datas divergentes"],
                    "required_visual_elements": ["Documentos de apreensão, tela de aviso do Departamento de Justiça"],
                    "evidence_needed": ["document", "photograph"],
                    "visual_impact_potential": "very_high"
                },
                {
                    "scene_id": 3,
                    "original_narration": self.sample_doc_script["scenes"][2]["narration"],
                    "scene_intent": "Concluir com a lição estrutural e a transformação histórica",
                    "narrative_purpose": "transformation",
                    "predominant_emotion": "trust",
                    "emotional_intensity": 0.6,
                    "narrative_tension": 0.4,
                    "dramatic_importance": 0.75,
                    "factual_assertions": ["A apreensão influenciou os padrões de criptografia moderna"],
                    "narrative_inferences": ["O evento acelerou a adoção em massa de canais com criptografia de ponta a ponta"],
                    "visual_proof_requirements": ["Gráficos de evolução de adoção de protocolos criptográficos"],
                    "forbidden_fabrications": ["Não atribuir tecnologias modernas a períodos anteriores"],
                    "required_visual_elements": ["Luzes de datacenter moderno, interface de segurança digital"],
                    "evidence_needed": ["data_metric"],
                    "visual_impact_potential": "medium"
                }
            ]
        }))

        analyzer = NarrativeAnalyzer(llm_client=analyzer_mock)
        analysis_res = analyzer.analyze(self.sample_doc_script)

        self.assertEqual(analysis_res.total_scenes, 3)
        self.assertEqual(analysis_res.central_theme, "Origem e impacto do Silk Road e da privacidade digital")
        self.assertEqual(len(analysis_res.scene_analyses), 3)
        self.assertEqual(analysis_res.scene_analyses[0].narrative_purpose, NarrativePurpose.HOOK)

        # Alimenta o VisualDirector
        director_mock = create_autospec(LLMClient, instance=True)
        director_mock.complete.return_value = MockLLMResponse(json.dumps({
            "visual_bible": {
                "visual_language": "investigative",
                "dominant_style": "dark_noir",
                "color_palette": ["#0A0E17", "#1A2639", "#2E5B88", "#C5D1EB", "#00FF66"],
                "lighting_mood": "dark_cinematic",
                "pacing_rhythm": "slow_burn"
            },
            "scene_directions": [
                {
                    "scene_id": 1,
                    "narration": self.sample_doc_script["scenes"][0]["narration"],
                    "what_viewer_sees": "Dark atmospheric server room with flashing green indicator LEDs",
                    "dominant_element": "Towering rack of encrypted servers",
                    "visual_production_type": "stock_footage",
                    "camera_framing": "wide",
                    "camera_movement": "slow_push_in",
                    "depth_of_field": "deep_focus",
                    "composition": "leading_lines",
                    "lighting_mood": "dark_cinematic",
                    "atmosphere": "eerie_mystery",
                    "color_palette_notes": "Deep navy blues and emerald status lights",
                    "transition_in": "direct_cut",
                    "transition_out": "crossfade_slow",
                    "visual_connection_previous": None,
                    "visual_connection_next": "Cuts from cold server room to tactical federal operations",
                    "continuity_anchor": "digital_underworld",
                    "b_roll_visual": "Dark server room filled with massive mainframe racks blinking green and cyan status lights subtle haze slow dolly in",
                    "b_roll_query": "dark server room blinking lights",
                    "sfx_cue": "low_data_drone",
                    "on_screen_text": "DEEP WEB",
                    "estimated_cost_tier": 1
                },
                {
                    "scene_id": 2,
                    "narration": self.sample_doc_script["scenes"][1]["narration"],
                    "what_viewer_sees": "High contrast close up of official federal seizure banner and law enforcement agents",
                    "dominant_element": "Department of Justice seizure notice on screen",
                    "visual_production_type": "cinematic_still",
                    "camera_framing": "close_up",
                    "camera_movement": "impact_micro_shake",
                    "depth_of_field": "shallow",
                    "composition": "central_focus",
                    "lighting_mood": "high_contrast_dramatic",
                    "atmosphere": "tense_confrontation",
                    "color_palette_notes": "Harsh cold whites and amber incident lights",
                    "transition_in": "crossfade_slow",
                    "transition_out": "direct_cut",
                    "visual_connection_previous": "Escalation from passive data to active raid",
                    "visual_connection_next": "Resolution into the aftermath of digital architecture",
                    "continuity_anchor": "fbi_raid_2013",
                    "b_roll_visual": "Archival close up of law enforcement tactical computer seizure screen warning red seal high contrast grain",
                    "b_roll_query": "police computer forensic seizure screen",
                    "sfx_cue": "heavy_impact_thud",
                    "on_screen_text": "OCTOBER 2013",
                    "estimated_cost_tier": 2
                },
                {
                    "scene_id": 3,
                    "narration": self.sample_doc_script["scenes"][2]["narration"],
                    "what_viewer_sees": "Futuristic clean cryptographic data streams connecting across an illuminated globe",
                    "dominant_element": "Luminescent security shield topology over world map",
                    "visual_production_type": "motion_graphic",
                    "camera_framing": "extreme_wide",
                    "camera_movement": "crane_sweep",
                    "depth_of_field": "deep_focus",
                    "composition": "symmetrical",
                    "lighting_mood": "dark_cinematic",
                    "atmosphere": "architectural_clarity",
                    "color_palette_notes": "Sleek obsidian background with gold cryptographic vectors",
                    "transition_in": "direct_cut",
                    "transition_out": "fade_to_black",
                    "visual_connection_previous": "Transition from seizure to systemic change",
                    "visual_connection_next": None,
                    "continuity_anchor": "digital_sovereignty",
                    "b_roll_visual": "Abstract motion graphics of glowing cryptographic nodes interconnecting across dark digital topography smooth crane sweep",
                    "b_roll_query": "cryptography data nodes dark topography",
                    "sfx_cue": "crystalline_resolve",
                    "on_screen_text": None,
                    "estimated_cost_tier": 1
                }
            ]
        }))

        director = VisualDirector(llm_client=director_mock)
        visual_res = director.direct(analysis_res)

        self.assertEqual(len(visual_res.scene_directions), 3)
        self.assertEqual(visual_res.visual_bible.visual_language, VisualLanguage.INVESTIGATIVE)
        self.assertEqual(visual_res.scene_directions[0].camera_framing, CameraFraming.WIDE)
        self.assertEqual(visual_res.scene_directions[1].camera_movement, CameraMovement.IMPACT_MICRO_SHAKE)

    # ─────────────────────────────────────────────────────────────
    # 2. Roteiro com Cenas de Forte Tensão
    # ─────────────────────────────────────────────────────────────
    def test_02_high_tension_script(self):
        """2. Roteiro de forte tensão deve registrar alta intensidade e escolhas visuais tensas."""
        tense_script = [
            {"id": 1, "narration": "Às 21h07, um alarme ensurdecedor disparou no módulo de comando da Apollo 13."},
            {"id": 2, "narration": "O tanque de oxigênio número dois acabava de explodir, e o oxigênio da cabine estava vazando rapidamente no vácuo espacial."}
        ]

        analyzer_mock = create_autospec(LLMClient, instance=True)
        analyzer_mock.complete.return_value = MockLLMResponse(json.dumps({
            "central_theme": "A crise de oxigênio da missão Apollo 13",
            "historical_or_factual_context": "Abril de 1970, espaço lunar",
            "narrative_objective": "Transmitir a urgência extrema e a luta pela sobrevivência dos astronautas",
            "narrative_progression": [
                "Disparo da falha catastrófica",
                "Vazamento iminente de oxigênio no vácuo",
                "Mobilização emergencial da tripulação e controle"
            ],
            "dominant_tone": "high_tension_survival",
            "scene_analyses": [
                {
                    "scene_id": 1,
                    "original_narration": tense_script[0]["narration"],
                    "scene_intent": "Instante exato de ruptura da normalidade e início da crise",
                    "narrative_purpose": "tension",
                    "predominant_emotion": "shock",
                    "emotional_intensity": 0.95,
                    "narrative_tension": 0.98,
                    "dramatic_importance": 1.0,
                    "factual_assertions": ["Um alarme disparou no módulo de comando às 21h07"],
                    "narrative_inferences": ["Os astronautas foram pegos em surpresa absoluta"],
                    "visual_proof_requirements": ["Gravação de áudio da telemetria, painel de advertência do módulo Odyssey"],
                    "forbidden_fabrications": ["Não inventar explosão de fogo no vácuo sem física realista"],
                    "required_visual_elements": ["Luzes de alerta piscando no cockpit analógico"],
                    "evidence_needed": ["telemetry", "object"],
                    "visual_impact_potential": "very_high"
                },
                {
                    "scene_id": 2,
                    "original_narration": tense_script[1]["narration"],
                    "scene_intent": "Evidenciar a gravidade mortal da perda contínua de suporte de vida",
                    "narrative_purpose": "tension",
                    "predominant_emotion": "fear",
                    "emotional_intensity": 1.0,
                    "narrative_tension": 1.0,
                    "dramatic_importance": 1.0,
                    "factual_assertions": ["O tanque de oxigênio 2 explodiu", "Oxigênio vazava no espaço"],
                    "narrative_inferences": ["Cada segundo sem contenção reduzia as chances de retorno à Terra"],
                    "visual_proof_requirements": ["Fotos reais pós-acidente do módulo de serviço desfigurado"],
                    "forbidden_fabrications": ["Não alterar os parâmetros oficiais do relatório de acidente da NASA"],
                    "required_visual_elements": ["Nuvem de gás branco escapando na escuridão do espaço"],
                    "evidence_needed": ["photograph", "telemetry"],
                    "visual_impact_potential": "very_high"
                }
            ]
        }))

        analyzer = NarrativeAnalyzer(llm_client=analyzer_mock)
        res = analyzer.analyze(tense_script)

        self.assertGreaterEqual(res.scene_analyses[0].emotional_intensity, 0.9)
        self.assertGreaterEqual(res.scene_analyses[1].narrative_tension, 0.95)
        self.assertEqual(res.scene_analyses[1].predominant_emotion, Emotion.FEAR)

    # ─────────────────────────────────────────────────────────────
    # 3. Roteiro Histórico
    # ─────────────────────────────────────────────────────────────
    def test_03_historical_script(self):
        """3. Roteiro histórico deve exigir comprovação documental e diferenciar fato de inferência."""
        hist_script = [
            {"id": 1, "narration": "Em 20 de julho de 1969, Neil Armstrong pisou na Lua, cumprindo a promessa de John F. Kennedy."}
        ]

        analyzer_mock = create_autospec(LLMClient, instance=True)
        analyzer_mock.complete.return_value = MockLLMResponse(json.dumps({
            "central_theme": "Alunissagem da Apollo 11",
            "historical_or_factual_context": "Guerra Fria, Corrida Espacial, 20 de Julho de 1969",
            "narrative_objective": "Documentar o ápice da corrida espacial",
            "narrative_progression": [
                "Lançamento épico em Cabo Canaveral",
                "Primeiro passo humano no solo lunar",
                "Retorno triunfal e legado civilizatório"
            ],
            "dominant_tone": "historical_epic",
            "scene_analyses": [
                {
                    "scene_id": 1,
                    "original_narration": hist_script[0]["narration"],
                    "scene_intent": "Consagrar o momento histórico máximo da exploração espacial",
                    "narrative_purpose": "historical_evidence",
                    "predominant_emotion": "hope",
                    "emotional_intensity": 0.85,
                    "narrative_tension": 0.5,
                    "dramatic_importance": 0.95,
                    "factual_assertions": ["Neil Armstrong pisou na Lua em 20 de julho de 1969", "Kennedy havia prometido o feito"],
                    "narrative_inferences": ["O evento simbolizou a supremacia tecnológica americana na época"],
                    "visual_proof_requirements": ["Gravação de vídeo 16mm da câmera de TV do LEM, foto oficial da pegada"],
                    "forbidden_fabrications": ["Não mostrar trajes espaciais de missões modernas Shuttle ou Orion"],
                    "required_visual_elements": ["Bota A7L pressionando o regolito lunar"],
                    "evidence_needed": ["photograph", "document"],
                    "visual_impact_potential": "very_high"
                }
            ]
        }))

        analyzer = NarrativeAnalyzer(llm_client=analyzer_mock)
        res = analyzer.analyze(hist_script)

        scene = res.scene_analyses[0]
        self.assertEqual(scene.narrative_purpose, NarrativePurpose.HISTORICAL_EVIDENCE)
        self.assertIn("Neil Armstrong", scene.factual_assertions[0])
        self.assertIn("A7L", scene.required_visual_elements[0])
        self.assertIn("Shuttle", scene.forbidden_fabrications[0])

    # ─────────────────────────────────────────────────────────────
    # 4. Roteiro Investigativo
    # ─────────────────────────────────────────────────────────────
    def test_04_investigative_script(self):
        """4. Roteiro investigativo deve focar em evidências, documentos e revelações de fatos."""
        inv_script = [
            {"id": 1, "narration": "Auditores federais descobriram três contas secretas na Suíça operadas por laranjas da diretoria."}
        ]

        analyzer_mock = create_autospec(LLMClient, instance=True)
        analyzer_mock.complete.return_value = MockLLMResponse(json.dumps({
            "central_theme": "Corrupção financeira internacional",
            "historical_or_factual_context": "Auditoria de crimes de colarinho branco",
            "narrative_objective": "Revelar a teia de contas bancárias ocultas",
            "narrative_progression": [
                "Início das auditorias fiscais sigilosas",
                "Descoberta das contas bancárias na Suíça",
                "Comprovação documental do esquema de desvios"
            ],
            "dominant_tone": "investigative",
            "scene_analyses": [
                {
                    "scene_id": 1,
                    "original_narration": inv_script[0]["narration"],
                    "scene_intent": "Apresentar a prova material irrefutável da fraude",
                    "narrative_purpose": "revelation",
                    "predominant_emotion": "shock",
                    "emotional_intensity": 0.8,
                    "narrative_tension": 0.85,
                    "dramatic_importance": 0.9,
                    "factual_assertions": ["Auditores federais localizaram 3 contas na Suíça"],
                    "narrative_inferences": ["A operação envolvia conivência estruturada de operadores internacionais"],
                    "visual_proof_requirements": ["Extratos bancários carimbados, números de contas cifrados"],
                    "forbidden_fabrications": ["Não inventar nomes reais sem respaldo no inquérito oficial"],
                    "required_visual_elements": ["Pastas de auditoria, documentos financeiros com marca-texto amarelo"],
                    "evidence_needed": ["document", "data_metric"],
                    "visual_impact_potential": "high"
                }
            ]
        }))

        analyzer = NarrativeAnalyzer(llm_client=analyzer_mock)
        res = analyzer.analyze(inv_script)
        self.assertEqual(res.scene_analyses[0].narrative_purpose, NarrativePurpose.REVELATION)
        self.assertIn(EvidenceType.DOCUMENT, res.scene_analyses[0].evidence_needed)

    # ─────────────────────────────────────────────────────────────
    # 5. Roteiro com Informações Insuficientes ou Vazias
    # ─────────────────────────────────────────────────────────────
    def test_05_insufficient_or_empty_information(self):
        """5. Roteiros vazios ou com narração ausente devem ser sumariamente rejeitados via ValueError."""
        analyzer = NarrativeAnalyzer()
        director = VisualDirector()

        # Entrada nula
        with self.assertRaises(ValueError):
            analyzer.analyze(None)

        with self.assertRaises(ValueError):
            director.direct(None)

        # Entrada com lista de cenas vazia
        with self.assertRaises(ValueError):
            analyzer.analyze({"scenes": []})

        with self.assertRaises(ValueError):
            analyzer.analyze([])

        # Cena com texto em branco ou somente espaços
        with self.assertRaises(ValueError):
            analyzer.analyze([{"id": 1, "narration": "   "}])

        with self.assertRaises(ValueError):
            analyzer.analyze([{"id": 1, "narration": "\n\t   "}])

    # ─────────────────────────────────────────────────────────────
    # 6. Resposta Inválida do LLM (Schema Incorreto)
    # ─────────────────────────────────────────────────────────────
    def test_06_invalid_llm_response_schema(self):
        """6. Quando o LLM retorna schema divergente ou campos obrigatórios faltantes, deve lançar erro explícito."""
        analyzer_mock = create_autospec(LLMClient, instance=True)
        # Retorna objeto sem 'scene_analyses'
        analyzer_mock.complete.return_value = MockLLMResponse(json.dumps({
            "central_theme": "Algum tema",
            "narrative_objective": "Algum objetivo"
        }))

        analyzer = NarrativeAnalyzer(llm_client=analyzer_mock)
        with self.assertRaises(NarrativeAnalysisError) as ctx:
            analyzer.analyze(self.sample_doc_script)
        self.assertIn("missing 'scene_analyses'", str(ctx.exception).lower())

        # Discrepância na contagem de cenas (mandou 3, LLM devolveu 1)
        analyzer_mock.complete.return_value = MockLLMResponse(json.dumps({
            "central_theme": "Tema",
            "historical_or_factual_context": "Contexto",
            "narrative_objective": "Objetivo",
            "narrative_progression": ["Etapa 1", "Etapa 2", "Etapa 3"],
            "scene_analyses": [
                {
                    "scene_id": 1,
                    "original_narration": "texto",
                    "scene_intent": "intent",
                }
            ]
        }))
        with self.assertRaises(NarrativeAnalysisError) as ctx:
            analyzer.analyze(self.sample_doc_script)
        self.assertIn("count mismatch", str(ctx.exception).lower())

    # ─────────────────────────────────────────────────────────────
    # 7. JSON Malformado do LLM
    # ─────────────────────────────────────────────────────────────
    def test_07_malformed_json_response(self):
        """7. LLM retornando JSON corrompido que não pode ser reparado deve disparar NarrativeAnalysisError."""
        analyzer_mock = create_autospec(LLMClient, instance=True)
        analyzer_mock.complete.return_value = MockLLMResponse("Desculpe, como um modelo de IA eu não posso responder em JSON: { corrupted [}")

        analyzer = NarrativeAnalyzer(llm_client=analyzer_mock)
        with self.assertRaises(NarrativeAnalysisError) as ctx:
            analyzer.analyze(self.sample_doc_script)
        self.assertIn("malformed json", str(ctx.exception).lower())

        # Teste idêntico para o VisualDirector
        sample_analysis = self._create_sample_narrative_analysis()
        director_mock = create_autospec(LLMClient, instance=True)
        director_mock.complete.return_value = MockLLMResponse("Not a JSON at all 12345")
        director = VisualDirector(llm_client=director_mock)

        with self.assertRaises(VisualDirectionError) as ctx:
            director.direct(sample_analysis)
        self.assertIn("malformed json", str(ctx.exception).lower())

    # ─────────────────────────────────────────────────────────────
    # 8. Timeout do Provedor e Falhas de Rede
    # ─────────────────────────────────────────────────────────────
    def test_08_provider_timeout_and_network_failures(self):
        """8. Exceções de rede ou timeout da API devem ser encapsuladas sem vazamento de segredos."""
        analyzer_mock = create_autospec(LLMClient, instance=True)
        analyzer_mock.complete.side_effect = TimeoutError("Request timed out connecting to api.openai.com?key=sk-SECRET1234567890abcdef")

        analyzer = NarrativeAnalyzer(llm_client=analyzer_mock, timeout=30.0)
        with self.assertRaises(NarrativeAnalysisError) as ctx:
            analyzer.analyze(self.sample_doc_script)

        # Garante que o segredo foi higienizado nos logs/mensagens
        self.assertNotIn("sk-SECRET1234567890abcdef", str(ctx.exception))
        self.assertIn("communication error", str(ctx.exception).lower())

    # ─────────────────────────────────────────────────────────────
    # 9. Compatibilidade com o Contrato Canônico V2
    # ─────────────────────────────────────────────────────────────
    def test_09_v2_canonical_contract_compatibility(self):
        """9. As direções visuais devem converter para cenas canônicas V2 aprovadas pelo Scene Normalizer."""
        director_mock = create_autospec(LLMClient, instance=True)
        director_mock.complete.return_value = MockLLMResponse(json.dumps({
            "visual_bible": {
                "visual_language": "cinematic",
                "dominant_style": "dark_noir",
                "color_palette": ["#000000", "#111111", "#FFFFFF"],
                "lighting_mood": "dark_cinematic",
                "pacing_rhythm": "slow_burn"
            },
            "scene_directions": [
                {
                    "scene_id": 1,
                    "narration": "A inteligência artificial transformou a produção cinematográfica.",
                    "what_viewer_sees": "High tech holographic interface projecting virtual movie cameras",
                    "dominant_element": "Luminous holographic camera rig",
                    "visual_production_type": "ai_video",
                    "camera_framing": "medium",
                    "camera_movement": "tracking_lateral",
                    "depth_of_field": "shallow",
                    "composition": "rule_of_thirds",
                    "lighting_mood": "dark_cinematic",
                    "atmosphere": "futuristic",
                    "color_palette_notes": "Neon cyan and deep obsidian",
                    "transition_in": "direct_cut",
                    "transition_out": "whip_pan_whoosh",
                    "visual_connection_previous": None,
                    "visual_connection_next": "Whip pan to editing suite",
                    "continuity_anchor": "holocamera_01",
                    "b_roll_visual": "Futuristic virtual cinematography rig with neon holographic lenses rotating smoothly in dark studio",
                    "b_roll_query": "virtual cinematography camera studio",
                    "sfx_cue": "whoosh_digital",
                    "on_screen_text": "VIRTUAL CINEMA",
                    "estimated_cost_tier": 2
                }
            ]
        }))

        sample_analysis = self._create_sample_narrative_analysis()

        director = VisualDirector(llm_client=director_mock)
        vis_result = director.direct(sample_analysis)

        # Converte para modelos canônicos Scene V2
        canonical_scenes = vis_result.to_canonical_scenes()
        self.assertEqual(len(canonical_scenes), 1)

        scene = canonical_scenes[0]
        self.assertIsInstance(scene, Scene)
        self.assertEqual(scene.id, 1)
        self.assertEqual(scene.narration, "A inteligência artificial transformou a produção cinematográfica.")
        self.assertEqual(scene.camera_framing, CameraFraming.MEDIUM)
        self.assertEqual(scene.camera_movement, CameraMovement.TRACKING_LATERAL)
        self.assertEqual(scene.visual_production_type, VisualProductionType.AI_VIDEO)

        # Valida round-trip com o normalizador V2 oficial
        scene_dict = scene.model_dump(mode="json", by_alias=False)
        normalized = normalize_scene_v2(scene_dict)
        self.assertEqual(normalized["id"], 1)
        self.assertEqual(normalized["narration"], scene.narration)
        self.assertEqual(normalized["camera_movement"], "tracking_lateral")

    # ─────────────────────────────────────────────────────────────
    # 10. Preservação Integral do Roteiro Original
    # ─────────────────────────────────────────────────────────────
    def test_10_immutable_original_narration_preservation(self):
        """10. Mesmo se o LLM tentar parafrasear ou mutilar a narração, o Python restaura o texto original idêntico."""
        original_text = "Esta é a narração original exata que NUNCA deve sofrer mutação por nenhuma IA."
        script_input = [{"id": 1, "narration": original_text}]

        analyzer_mock = create_autospec(LLMClient, instance=True)
        # LLM tenta alucinar e mudar a narração para inglês parafraseado
        analyzer_mock.complete.return_value = MockLLMResponse(json.dumps({
            "central_theme": "Tema",
            "historical_or_factual_context": "Contexto",
            "narrative_objective": "Objetivo",
            "narrative_progression": ["Etapa de entrada", "Etapa de teste", "Etapa de validação"],
            "scene_analyses": [
                {
                    "scene_id": 1,
                    "original_narration": "PARAPHRASED HALLUCINATED TEXT BY LLM",
                    "scene_intent": "Demonstrar integridade de texto",
                    "narrative_purpose": "context",
                    "predominant_emotion": "trust",
                    "emotional_intensity": 0.5,
                    "narrative_tension": 0.5,
                    "dramatic_importance": 0.5,
                    "factual_assertions": [],
                    "narrative_inferences": [],
                    "visual_proof_requirements": [],
                    "forbidden_fabrications": [],
                    "required_visual_elements": [],
                    "evidence_needed": [],
                    "visual_impact_potential": "medium"
                }
            ]
        }))

        analyzer = NarrativeAnalyzer(llm_client=analyzer_mock)
        analysis_res = analyzer.analyze(script_input)

        # Python forçou a restauração incondicional do original
        self.assertEqual(analysis_res.scene_analyses[0].original_narration, original_text)

        # O mesmo deve se manter no VisualDirector
        director_mock = create_autospec(LLMClient, instance=True)
        director_mock.complete.return_value = MockLLMResponse(json.dumps({
            "visual_bible": {
                "visual_language": "cinematic",
                "dominant_style": "cinematic_style",
                "color_palette": ["#000", "#FFF"],
                "lighting_mood": "dark",
                "pacing_rhythm": "slow"
            },
            "scene_directions": [
                {
                    "scene_id": 1,
                    "narration": "ANOTHER ATTEMPT TO MUTATE THE SCRIPT",
                    "what_viewer_sees": "Visual representation",
                    "dominant_element": "Element",
                    "b_roll_visual": "Detailed cinematic prompt",
                    "b_roll_query": "cinematic prompt"
                }
            ]
        }))

        director = VisualDirector(llm_client=director_mock)
        vis_res = director.direct(analysis_res)

        self.assertEqual(vis_res.scene_directions[0].narration, original_text)
        canonical_scenes = vis_res.to_canonical_scenes()
        self.assertEqual(canonical_scenes[0].narration, original_text)

    # ─────────────────────────────────────────────────────────────
    # 11. Determinismo das Validações Python
    # ─────────────────────────────────────────────────────────────
    def test_11_deterministic_python_validations(self):
        """11. Aliases como 'shake' e queries excessivamente longas devem ser deterministicamente normalizadas."""
        director_mock = create_autospec(LLMClient, instance=True)
        director_mock.complete.return_value = MockLLMResponse(json.dumps({
            "visual_bible": {
                "visual_language": "tense",
                "dominant_style": "high_contrast",
                "color_palette": ["#111", "#999"],
                "lighting_mood": "dark",
                "pacing_rhythm": "fast"
            },
            "scene_directions": [
                {
                    "scene_id": 1,
                    "narration": "O impacto abalou as estruturas.",
                    "what_viewer_sees": "Trembling walls",
                    "dominant_element": "Cracked concrete",
                    "camera_framing": "WIDE",
                    "camera_movement": "shake",  # Alias legado para impact_micro_shake
                    "b_roll_visual": "Extreme close up of concrete beam cracking under heavy seismic pressure slow motion 4k",
                    "b_roll_query": "concrete beam cracking under extreme seismic pressure slow motion ultra realistic",  # > 5 palavras
                }
            ]
        }))

        tense_scene = SceneNarrativeInterpretation(
            scene_id=1,
            original_narration="O impacto abalou as estruturas.",
            scene_intent="Impacto sísmico",
            narrative_purpose=NarrativePurpose.TENSION,
            predominant_emotion=Emotion.SHOCK,
            emotional_intensity=0.9,
            narrative_tension=0.9,
            dramatic_importance=0.8,
            visual_impact_potential=VisualImpactPotential.VERY_HIGH,
        )
        sample_analysis = self._create_sample_narrative_analysis(scenes=[tense_scene])

        director = VisualDirector(llm_client=director_mock)
        vis_res = director.direct(sample_analysis)

        # Alias 'shake' deve ter sido normalizado deterministicamente para 'impact_micro_shake'
        self.assertEqual(vis_res.scene_directions[0].camera_movement, CameraMovement.IMPACT_MICRO_SHAKE)
        # Query de >5 palavras deve ter sido normalizada para entre 2 e 5 palavras
        query_words = vis_res.scene_directions[0].b_roll_query.split()
        self.assertTrue(2 <= len(query_words) <= 5)

    # ─────────────────────────────────────────────────────────────
    # 12. Ausência de Mutação dos Objetos de Entrada
    # ─────────────────────────────────────────────────────────────
    def test_12_absence_of_input_mutation(self):
        """12. Objetos de entrada (dicionários e modelos) não sofrem mutação durante a análise e direção."""
        original_dict = {
            "title": "Documentário de Teste de Imutabilidade",
            "scenes": [
                {"id": 1, "narration": "Cena 1 para teste profundo.", "custom_meta": {"locked": True}},
                {"id": 2, "narration": "Cena 2 com metadados adicionais.", "tags": ["alpha", "beta"]}
            ]
        }
        dict_snapshot = copy.deepcopy(original_dict)

        analyzer_mock = create_autospec(LLMClient, instance=True)
        analyzer_mock.complete.return_value = MockLLMResponse(json.dumps({
            "central_theme": "Tema",
            "historical_or_factual_context": "Contexto",
            "narrative_objective": "Objetivo",
            "narrative_progression": ["Introdução", "Desenvolvimento", "Conclusão"],
            "scene_analyses": [
                {
                    "scene_id": 1,
                    "original_narration": original_dict["scenes"][0]["narration"],
                    "scene_intent": "Intent 1",
                },
                {
                    "scene_id": 2,
                    "original_narration": original_dict["scenes"][1]["narration"],
                    "scene_intent": "Intent 2",
                }
            ]
        }))

        analyzer = NarrativeAnalyzer(llm_client=analyzer_mock)
        analysis_res = analyzer.analyze(original_dict)

        # O dicionário original em memória permanece estritamente intocado
        self.assertEqual(original_dict, dict_snapshot)

        # Testa imutabilidade também sobre a análise narrativa no VisualDirector
        analysis_snapshot = copy.deepcopy(analysis_res)

        director_mock = create_autospec(LLMClient, instance=True)
        director_mock.complete.return_value = MockLLMResponse(json.dumps({
            "visual_bible": {
                "visual_language": "cinematic",
                "dominant_style": "cinematic",
                "color_palette": ["#000", "#111"],
                "lighting_mood": "dark",
                "pacing_rhythm": "slow"
            },
            "scene_directions": [
                {
                    "scene_id": 1,
                    "narration": original_dict["scenes"][0]["narration"],
                    "what_viewer_sees": "Vis 1",
                    "dominant_element": "Elem 1",
                    "b_roll_visual": "Prompt 1",
                    "b_roll_query": "query one"
                },
                {
                    "scene_id": 2,
                    "narration": original_dict["scenes"][1]["narration"],
                    "what_viewer_sees": "Vis 2",
                    "dominant_element": "Elem 2",
                    "b_roll_visual": "Prompt 2",
                    "b_roll_query": "query two"
                }
            ]
        }))

        director = VisualDirector(llm_client=director_mock)
        director.direct(analysis_res)

        # O modelo analysis_res permaneceu inalterado
        self.assertEqual(analysis_res.model_dump(), analysis_snapshot.model_dump())

    # ─────────────────────────────────────────────────────────────
    # 13. Correção 1 — Preservação Exata da Narração (Boundary)
    # ─────────────────────────────────────────────────────────────
    def test_13_exact_narration_preservation_boundary(self):
        """13. Narração com espaços iniciais/finais, tabs, quebras de linha e Unicode é preservada exatamente sem .strip()."""
        raw_complex_narration = "  \t\nEm 1986, o reator 4 de Chernobyl explodiu.\n\tA radiação se espalhou por toda a Europa! ⚠️☢️  \n"
        
        # 1. Pydantic model preserva exatamente
        scene_interp = SceneNarrativeInterpretation(
            scene_id=1,
            original_narration=raw_complex_narration,
            scene_intent="Impacto inicial do desastre",
        )
        self.assertEqual(scene_interp.original_narration, raw_complex_narration)
        self.assertTrue(scene_interp.original_narration.startswith("  \t\n"))
        self.assertTrue(scene_interp.original_narration.endswith("  \n"))

        # 2. Rejeição de narração contendo somente whitespace
        with self.assertRaises(ValidationError):
            SceneNarrativeInterpretation(
                scene_id=2,
                original_narration="   \t\n   ",
                scene_intent="Tentativa com espaços",
            )

        # 3. Pipeline completo no NarrativeAnalyzer e VisualDirector
        script_input = [{"id": 1, "narration": raw_complex_narration}]
        analyzer_mock = create_autospec(LLMClient, instance=True)
        analyzer_mock.complete.return_value = MockLLMResponse(json.dumps({
            "central_theme": "Chernobyl",
            "historical_or_factual_context": "1986",
            "narrative_objective": "Descrever o desastre",
            "narrative_progression": ["Explosão", "Vazamento", "Evacuação"],
            "scene_analyses": [
                {
                    "scene_id": 1,
                    "original_narration": "PARAPHRASED BY LLM",
                    "scene_intent": "Intenção",
                }
            ]
        }))

        analyzer = NarrativeAnalyzer(llm_client=analyzer_mock)
        analysis_res = analyzer.analyze(script_input)
        self.assertEqual(analysis_res.scene_analyses[0].original_narration, raw_complex_narration)

        director_mock = create_autospec(LLMClient, instance=True)
        director_mock.complete.return_value = MockLLMResponse(json.dumps({
            "visual_bible": {
                "visual_language": "historical",
                "dominant_style": "archive",
                "color_palette": ["#111", "#222"],
                "lighting_mood": "somber",
                "pacing_rhythm": "slow"
            },
            "scene_directions": [
                {
                    "scene_id": 1,
                    "narration": "DIFFERENT TEXT FROM LLM",
                    "what_viewer_sees": "Reactor core smoking",
                    "dominant_element": "Smoke plume",
                    "b_roll_visual": "Archival footage of reactor 4 ruins",
                    "b_roll_query": "chernobyl reactor ruins"
                }
            ]
        }))

        director = VisualDirector(llm_client=director_mock)
        vis_res = director.direct(analysis_res)
        self.assertEqual(vis_res.scene_directions[0].narration, raw_complex_narration)

        canonical = vis_res.to_canonical_scenes()
        self.assertEqual(canonical[0].narration, raw_complex_narration)

    # ─────────────────────────────────────────────────────────────
    # 14. Correção 2 — Timeout Real e Propagação nas Chamadas LLM
    # ─────────────────────────────────────────────────────────────
    def test_14_timeout_propagation_and_wrapping(self):
        """14. O parâmetro timeout é efetivamente propagado ao cliente LLM e encapsulado com a causa preservada."""
        client_mock = create_autospec(LLMClient, instance=True)
        client_mock.complete.return_value = MockLLMResponse(json.dumps({
            "central_theme": "Tema",
            "historical_or_factual_context": "Contexto",
            "narrative_objective": "Objetivo",
            "narrative_progression": ["Fase 1", "Fase 2", "Fase 3"],
            "scene_analyses": [
                {
                    "scene_id": 1,
                    "original_narration": "Narração de teste",
                    "scene_intent": "Intent",
                }
            ]
        }))

        # NarrativeAnalyzer com timeout de 45.0s
        analyzer = NarrativeAnalyzer(llm_client=client_mock, timeout=45.0)
        analyzer.analyze([{"id": 1, "narration": "Narração de teste"}])

        # Verifica que timeout=45.0 foi passado como argumento para client.complete
        client_mock.complete.assert_called_once()
        _, kwargs = client_mock.complete.call_args
        self.assertEqual(kwargs.get("timeout"), 45.0)

        # Falha de timeout no VisualDirector
        director_client_mock = create_autospec(LLMClient, instance=True)
        original_timeout_exc = TimeoutError("Connection to provider timed out after 15.0 seconds")
        director_client_mock.complete.side_effect = original_timeout_exc

        sample_analysis = self._create_sample_narrative_analysis()
        director = VisualDirector(llm_client=director_client_mock, timeout=15.0)

        with self.assertRaises(VisualDirectionError) as ctx:
            director.direct(sample_analysis)

        self.assertIn("timed out after 15.0s", str(ctx.exception).lower())
        # Causa original da exceção preservada
        self.assertIs(ctx.exception.__cause__, original_timeout_exc)

    # ─────────────────────────────────────────────────────────────
    # 15. Correção 3 — Não Mascarar Bíblias Visuais Inválidas
    # ─────────────────────────────────────────────────────────────
    def test_15_invalid_visual_bible_rejection_no_masking(self):
        """15. Bíblias Visuais ausentes, malformadas ou com enums inválidos disparam VisualDirectionError sem fallback."""
        sample_analysis = self._create_sample_narrative_analysis()

        # Cenário 1: visual_bible ausente
        director_mock_1 = create_autospec(LLMClient, instance=True)
        director_mock_1.complete.return_value = MockLLMResponse(json.dumps({
            "scene_directions": [
                {
                    "scene_id": 1,
                    "narration": sample_analysis.scene_analyses[0].original_narration,
                    "what_viewer_sees": "Interface",
                    "dominant_element": "Element",
                    "b_roll_visual": "Visual prompt",
                    "b_roll_query": "visual prompt"
                }
            ]
        }))
        director_1 = VisualDirector(llm_client=director_mock_1)
        with self.assertRaises(VisualDirectionError) as ctx:
            director_1.direct(sample_analysis)
        self.assertIn("missing 'visual_bible'", str(ctx.exception).lower())

        # Cenário 2: visual_language com enum inválido (sem fallback silencioso)
        director_mock_2 = create_autospec(LLMClient, instance=True)
        director_mock_2.complete.return_value = MockLLMResponse(json.dumps({
            "visual_bible": {
                "visual_language": "SUPER_HERO_CARTOONISH_NOT_ALLOWED",
                "dominant_style": "cinematic"
            },
            "scene_directions": [
                {
                    "scene_id": 1,
                    "narration": sample_analysis.scene_analyses[0].original_narration,
                    "what_viewer_sees": "Interface",
                    "dominant_element": "Element",
                    "b_roll_visual": "Visual prompt",
                    "b_roll_query": "visual prompt"
                }
            ]
        }))
        director_2 = VisualDirector(llm_client=director_mock_2)
        with self.assertRaises(VisualDirectionError) as ctx:
            director_2.direct(sample_analysis)
        self.assertIn("visual bible validation failed", str(ctx.exception).lower())

        # Cenário 3: color_palette vazia ou com tipos incorretos
        director_mock_3 = create_autospec(LLMClient, instance=True)
        director_mock_3.complete.return_value = MockLLMResponse(json.dumps({
            "visual_bible": {
                "visual_language": "cinematic",
                "dominant_style": "cinematic",
                "color_palette": [],  # Inválido: min_length=1
            },
            "scene_directions": [
                {
                    "scene_id": 1,
                    "narration": sample_analysis.scene_analyses[0].original_narration,
                    "what_viewer_sees": "Interface",
                    "dominant_element": "Element",
                    "b_roll_visual": "Visual prompt",
                    "b_roll_query": "visual prompt"
                }
            ]
        }))
        director_3 = VisualDirector(llm_client=director_mock_3)
        with self.assertRaises(VisualDirectionError):
            director_3.direct(sample_analysis)

    # ─────────────────────────────────────────────────────────────
    # 16. Correção 4 — Validação Estrita de b_roll_query
    # ─────────────────────────────────────────────────────────────
    def test_16_broll_query_boundary_and_validation(self):
        """16. b_roll_query exige estritamente entre 2 e 5 palavras significativas."""
        valid_queries = [
            "Apollo mission",                          # 2 palavras
            "abandoned nuclear facility",              # 3 palavras
            "historical financial documents archive",  # 4 palavras
            "deep ocean exploration trench submarine", # 5 palavras
        ]
        for q in valid_queries:
            dir_item = SceneVisualDirection(
                scene_id=1,
                narration="Texto de teste",
                what_viewer_sees="Visual",
                dominant_element="Foco",
                b_roll_visual="Cinematic prompt in English",
                b_roll_query=q,
            )
            self.assertEqual(len(dir_item.b_roll_query.split()), len(q.split()))

        # Consultas com excesso de espaços internos/externos são limpas sem mutação da narração
        spaced_query = SceneVisualDirection(
            scene_id=1,
            narration="  Narração original com espaços intactos  ",
            what_viewer_sees="Visual",
            dominant_element="Foco",
            b_roll_visual="Cinematic prompt",
            b_roll_query="   deep   ocean   exploration   ",
        )
        self.assertEqual(spaced_query.b_roll_query, "deep ocean exploration")
        self.assertEqual(spaced_query.narration, "  Narração original com espaços intactos  ")

        # Rejeições obrigatórias:
        invalid_queries = [
            "",                                                                             # Vazia
            "   ",                                                                          # Apenas espaços
            "A",                                                                            # 1 caractere
            "Apollo",                                                                       # 1 palavra
            "Apollo 11 historic mission lunar landing documentary archive footage extra",   # > 5 palavras
            "... --- ***",                                                                  # Pontuação pura sem caracteres alfanuméricos
        ]
        for inv_q in invalid_queries:
            with self.assertRaises(ValidationError):
                SceneVisualDirection(
                    scene_id=1,
                    narration="Texto válido",
                    what_viewer_sees="Visual",
                    dominant_element="Foco",
                    b_roll_visual="Cinematic prompt",
                    b_roll_query=inv_q,
                )

    # ─────────────────────────────────────────────────────────────
    # 17. Correção 5 — Validação Estrita de narrative_progression
    # ─────────────────────────────────────────────────────────────
    def test_17_narrative_progression_strict_boundaries(self):
        """17. narrative_progression exige estritamente entre 3 e 6 etapas, rejeitando vazias ou duplicadas."""
        sample_scenes = [
            SceneNarrativeInterpretation(
                scene_id=1,
                original_narration="Cena única",
                scene_intent="Intent",
            )
        ]

        # Válido: exatamente 3 etapas
        res_3 = NarrativeAnalysisResult(
            central_theme="Tema",
            historical_or_factual_context="Contexto",
            narrative_objective="Objetivo",
            narrative_progression=["Abertura", "Clímax", "Desfecho"],
            scene_analyses=sample_scenes,
        )
        self.assertEqual(len(res_3.narrative_progression), 3)

        # Válido: exatamente 6 etapas
        res_6 = NarrativeAnalysisResult(
            central_theme="Tema",
            historical_or_factual_context="Contexto",
            narrative_objective="Objetivo",
            narrative_progression=["Etapa 1", "Etapa 2", "Etapa 3", "Etapa 4", "Etapa 5", "Etapa 6"],
            scene_analyses=sample_scenes,
        )
        self.assertEqual(len(res_6.narrative_progression), 6)

        # Inválido: lista vazia
        with self.assertRaises(ValidationError):
            NarrativeAnalysisResult(
                central_theme="Tema",
                historical_or_factual_context="Contexto",
                narrative_objective="Objetivo",
                narrative_progression=[],
                scene_analyses=sample_scenes,
            )

        # Inválido: 1 etapa
        with self.assertRaises(ValidationError):
            NarrativeAnalysisResult(
                central_theme="Tema",
                historical_or_factual_context="Contexto",
                narrative_objective="Objetivo",
                narrative_progression=["Única etapa"],
                scene_analyses=sample_scenes,
            )

        # Inválido: 2 etapas
        with self.assertRaises(ValidationError):
            NarrativeAnalysisResult(
                central_theme="Tema",
                historical_or_factual_context="Contexto",
                narrative_objective="Objetivo",
                narrative_progression=["Primeira", "Segunda"],
                scene_analyses=sample_scenes,
            )

        # Inválido: 7 etapas
        with self.assertRaises(ValidationError):
            NarrativeAnalysisResult(
                central_theme="Tema",
                historical_or_factual_context="Contexto",
                narrative_objective="Objetivo",
                narrative_progression=["E1", "E2", "E3", "E4", "E5", "E6", "E7"],
                scene_analyses=sample_scenes,
            )

        # Inválido: etapa com string vazia ou apenas espaços
        with self.assertRaises(ValidationError):
            NarrativeAnalysisResult(
                central_theme="Tema",
                historical_or_factual_context="Contexto",
                narrative_objective="Objetivo",
                narrative_progression=["Início", "   ", "Fim"],
                scene_analyses=sample_scenes,
            )

        # Inválido: etapas duplicadas (mesmo com variação de case)
        with self.assertRaises(ValidationError):
            NarrativeAnalysisResult(
                central_theme="Tema",
                historical_or_factual_context="Contexto",
                narrative_objective="Objetivo",
                narrative_progression=["Início", "Clímax", "início"],
                scene_analyses=sample_scenes,
            )

    # ─────────────────────────────────────────────────────────────
    # 18. Correção 6 — Preservação de Dados na Conversão para Scene V2
    # ─────────────────────────────────────────────────────────────
    def test_18_canonical_v2_data_preservation_round_trip(self):
        """18. to_canonical_scenes() preserva durações customizadas (não substituindo por 5.0), timestamps e metadados."""
        original_scenes = [
            {
                "id": 1,
                "narration": "Cena com duração e timestamp específicos.",
                "duration_s": 12.5,
                "timestamp": "0:00-0:12.5",
                "scene_type": "problem",
                "custom_source": "documentary_archive_v1",
            },
            {
                "id": 2,
                "narration": "Segunda cena com duração calculada.",
                "duration_s": 8.0,
                "timestamp": "0:12.5-0:20.5",
                "scene_type": "solution",
                "custom_flag": True,
            }
        ]

        vis_result = VisualDirectionResult(
            visual_bible=VisualBible(
                visual_language=VisualLanguage.INVESTIGATIVE,
                dominant_style="investigative_documentary",
                color_palette=["#0B0C10", "#45A29E"],
                lighting_mood="dark_cinematic",
                pacing_rhythm="measured",
            ),
            scene_directions=[
                SceneVisualDirection(
                    scene_id=1,
                    narration="Cena com duração e timestamp específicos.",
                    what_viewer_sees="Archive footage",
                    dominant_element="Folder",
                    b_roll_visual="Cinematic visual prompt 1",
                    b_roll_query="archive documents folder",
                    duration_s=12.5,
                    timestamp="0:00-0:12.5",
                ),
                SceneVisualDirection(
                    scene_id=2,
                    narration="Segunda cena com duração calculada.",
                    what_viewer_sees="Solution schematic",
                    dominant_element="Blueprint",
                    b_roll_visual="Cinematic visual prompt 2",
                    b_roll_query="blueprint schematic solution",
                    duration_s=8.0,
                    timestamp="0:12.5-0:20.5",
                )
            ]
        )

        canonical_scenes = vis_result.to_canonical_scenes(original_scenes=original_scenes)
        self.assertEqual(len(canonical_scenes), 2)

        s1 = canonical_scenes[0]
        self.assertEqual(s1.id, 1)
        self.assertEqual(s1.duration_s, 12.5)  # Não foi substituído pelo default 5.0!
        self.assertEqual(s1.timestamp, "0:00-0:12.5")
        self.assertEqual(s1.scene_type, SceneType.PROBLEM)
        self.assertEqual(getattr(s1, "custom_source", None), "documentary_archive_v1")

        s2 = canonical_scenes[1]
        self.assertEqual(s2.id, 2)
        self.assertEqual(s2.duration_s, 8.0)
        self.assertEqual(s2.timestamp, "0:12.5-0:20.5")
        self.assertEqual(s2.scene_type, SceneType.SOLUTION)
        self.assertTrue(getattr(s2, "custom_flag", False))

    # ─────────────────────────────────────────────────────────────
    # 19. Correção 7 — EvidenceType e Validação Epistêmica
    # ─────────────────────────────────────────────────────────────
    def test_19_evidence_type_and_epistemic_validation(self):
        """19. Valida categorias estritas do Enum EvidenceType e rejeita confusão de inferências com fatos."""
        # Categorias válidas por string e alias
        scene = SceneNarrativeInterpretation(
            scene_id=1,
            original_narration="O cometa Halley foi registrado no ano de 1066.",
            scene_intent="Registro histórico",
            evidence_needed=["photo", "doc", "telemetry"],
        )
        self.assertEqual(scene.evidence_needed, [
            EvidenceType.PHOTOGRAPH,
            EvidenceType.DOCUMENT,
            EvidenceType.TELEMETRY,
        ])

        # Categoria inválida deve ser sumariamente rejeitada
        with self.assertRaises(ValidationError):
            SceneNarrativeInterpretation(
                scene_id=1,
                original_narration="Texto de teste",
                scene_intent="Intent",
                evidence_needed=["unsupported_evidence_type_xyz"],
            )

        # Violação epistêmica: inferência narrativa não pode ser afirmada como fato
        with self.assertRaises(ValidationError) as ctx:
            SceneNarrativeInterpretation(
                scene_id=1,
                original_narration="Texto de teste",
                scene_intent="Intent",
                factual_assertions=["O operador estava cansado e cometeu um erro intencional"],
                narrative_inferences=["O operador estava cansado e cometeu um erro intencional"],
            )
        self.assertIn("epistemic violation", str(ctx.exception).lower())

    # ─────────────────────────────────────────────────────────────
    # 20. Correção 8 — Validação de on_screen_text
    # ─────────────────────────────────────────────────────────────
    def test_20_on_screen_text_boundary_validation(self):
        """20. on_screen_text valida rigorosamente o limite de até 3 palavras e preserva acentuação."""
        # Casos válidos: None, 1 palavra, 2 palavras, 3 palavras (com acentos preservados)
        valid_cases = [
            (None, None),
            ("EXPLOSÃO", "EXPLOSÃO"),
            ("SILK ROAD", "SILK ROAD"),
            ("OPERAÇÃO CIBERNÉTICA 2013", "OPERAÇÃO CIBERNÉTICA 2013"),
        ]
        for val, expected in valid_cases:
            dir_item = SceneVisualDirection(
                scene_id=1,
                narration="Texto",
                what_viewer_sees="Visual",
                dominant_element="Foco",
                b_roll_visual="Prompt",
                b_roll_query="visual prompt",
                on_screen_text=val,
            )
            self.assertEqual(dir_item.on_screen_text, expected)

        # Inválido: 4 palavras (não pode ser truncado silenciosamente)
        with self.assertRaises(ValidationError) as ctx:
            SceneVisualDirection(
                scene_id=1,
                narration="Texto",
                what_viewer_sees="Visual",
                dominant_element="Foco",
                b_roll_visual="Prompt",
                b_roll_query="visual prompt",
                on_screen_text="FEDERAL BUREAU OF INVESTIGATION",
            )
        self.assertIn("exceeds maximum limit of 3 words", str(ctx.exception))

        # Inválido: string vazia ou apenas espaços
        with self.assertRaises(ValidationError):
            SceneVisualDirection(
                scene_id=1,
                narration="Texto",
                what_viewer_sees="Visual",
                dominant_element="Foco",
                b_roll_visual="Prompt",
                b_roll_query="visual prompt",
                on_screen_text="   ",
            )

    # ─────────────────────────────────────────────────────────────
    # 21. Correção 9 — Consistência de Cenas e Identificadores
    # ─────────────────────────────────────────────────────────────
    def test_21_scene_consistency_order_and_id_rejection(self):
        """21. Rejeita respostas do LLM com IDs duplicados, trocados, fora de ordem ou divergentes."""
        script_input = [
            {"id": 1, "narration": "Primeira cena do roteiro."},
            {"id": 2, "narration": "Segunda cena do roteiro."}
        ]

        # Cenário A: LLM inverte a ordem das cenas (devolve 2 depois 1)
        analyzer_mock = create_autospec(LLMClient, instance=True)
        analyzer_mock.complete.return_value = MockLLMResponse(json.dumps({
            "central_theme": "Tema",
            "historical_or_factual_context": "Contexto",
            "narrative_objective": "Objetivo",
            "narrative_progression": ["E1", "E2", "E3"],
            "scene_analyses": [
                {
                    "scene_id": 2,
                    "original_narration": script_input[1]["narration"],
                    "scene_intent": "Intent 2",
                },
                {
                    "scene_id": 1,
                    "original_narration": script_input[0]["narration"],
                    "scene_intent": "Intent 1",
                }
            ]
        }))

        analyzer = NarrativeAnalyzer(llm_client=analyzer_mock)
        with self.assertRaises(NarrativeAnalysisError) as ctx:
            analyzer.analyze(script_input)
        self.assertIn("scene id or order mismatch", str(ctx.exception).lower())

        # Cenário B: LLM devolve IDs duplicados
        analyzer_mock.complete.return_value = MockLLMResponse(json.dumps({
            "central_theme": "Tema",
            "historical_or_factual_context": "Contexto",
            "narrative_objective": "Objetivo",
            "narrative_progression": ["E1", "E2", "E3"],
            "scene_analyses": [
                {
                    "scene_id": 1,
                    "original_narration": script_input[0]["narration"],
                    "scene_intent": "Intent 1",
                },
                {
                    "scene_id": 1,
                    "original_narration": script_input[0]["narration"],
                    "scene_intent": "Intent 1 duplicada",
                }
            ]
        }))
        with self.assertRaises(NarrativeAnalysisError) as ctx:
            analyzer.analyze(script_input)
        self.assertIn("duplicate scene_ids", str(ctx.exception).lower())

        # Cenário C: LLM inventa IDs não existentes no roteiro de entrada
        analyzer_mock.complete.return_value = MockLLMResponse(json.dumps({
            "central_theme": "Tema",
            "historical_or_factual_context": "Contexto",
            "narrative_objective": "Objetivo",
            "narrative_progression": ["E1", "E2", "E3"],
            "scene_analyses": [
                {
                    "scene_id": 99,
                    "original_narration": script_input[0]["narration"],
                    "scene_intent": "Intent 99",
                },
                {
                    "scene_id": 100,
                    "original_narration": script_input[1]["narration"],
                    "scene_intent": "Intent 100",
                }
            ]
        }))
        with self.assertRaises(NarrativeAnalysisError) as ctx:
            analyzer.analyze(script_input)
        self.assertIn("scene id or order mismatch", str(ctx.exception).lower())

    # ─────────────────────────────────────────────────────────────
    # 22. Correção 10 — Sanitização Rigorosa de Credenciais
    # ─────────────────────────────────────────────────────────────
    def test_22_credential_sanitization_comprehensive(self):
        """22. Sanitizador remove todos os formatos de API keys, tokens, Bearer headers e senhas de URLs."""
        test_cases = [
            (
                "Error connecting: https://api.openai.com/v1/chat?key=sk-proj-abc1234567890defABCDEF1234&model=gpt-4",
                "Error connecting: https://api.openai.com/v1/chat?key=***&model=gpt-4"
            ),
            (
                "Gemini request failed: https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=AIzaSyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q",
                "Gemini request failed: https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=***"
            ),
            (
                "Request header: Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.token123",
                "Request header: Authorization: Bearer ***"
            ),
            (
                "URL with credentials: https://prod_user:SuperSecretPassword123@db.dark-channel.neon.tech:5432/main",
                "URL with credentials: https://prod_user:***@db.dark-channel.neon.tech:5432/main"
            ),
            (
                "Configuration dump: api_key='sk-1234567890abcdef1234567890abcdef' access_token=\"my_token_999\"",
                "Configuration dump: api_key='***' access_token=\"***\""
            ),
        ]
        for raw, expected in test_cases:
            sanitized = sanitize_secret(raw)
            self.assertEqual(sanitized, expected)
            self.assertNotIn("sk-proj-abc1234567890defABCDEF1234", sanitized)
            self.assertNotIn("AIzaSyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q", sanitized)
            self.assertNotIn("SuperSecretPassword123", sanitized)
            self.assertNotIn("my_token_999", sanitized)


if __name__ == "__main__":
    unittest.main()


class TestSprint22AuditFixes(unittest.TestCase):
    """Testes rigorosos da auditoria final do Sprint 2.2."""

    def _make_scene_analysis(self, scene_id: int = 1) -> SceneNarrativeInterpretation:
        return SceneNarrativeInterpretation(
            scene_id=scene_id,
            original_narration=f"Narração da cena {scene_id}",
            narrative_purpose=NarrativePurpose.CONTEXT,
            scene_intent="Apresentar contexto inicial da cena",
            predominant_emotion="curiosity",
            emotional_intensity=0.5,
            narrative_tension=0.5,
            dramatic_importance=0.5,
            visual_impact_potential="medium",
        )

    def test_total_scenes_exact_match_passes(self):
        res = NarrativeAnalysisResult(
            central_theme="Tema",
            historical_or_factual_context="Contexto",
            narrative_objective="Objetivo",
            narrative_progression=["Início", "Meio", "Fim"],
            scene_analyses=[self._make_scene_analysis(1), self._make_scene_analysis(2)],
            total_scenes=2,
        )
        self.assertEqual(res.total_scenes, 2)

    def test_total_scenes_greater_than_analyses_raises(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            NarrativeAnalysisResult(
                central_theme="Tema",
                historical_or_factual_context="Contexto",
                narrative_objective="Objetivo",
                narrative_progression=["Início", "Meio", "Fim"],
                scene_analyses=[self._make_scene_analysis(1)],
                total_scenes=5,
            )

    def test_total_scenes_less_than_analyses_raises(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            NarrativeAnalysisResult(
                central_theme="Tema",
                historical_or_factual_context="Contexto",
                narrative_objective="Objetivo",
                narrative_progression=["Início", "Meio", "Fim"],
                scene_analyses=[self._make_scene_analysis(1), self._make_scene_analysis(2)],
                total_scenes=1,
            )
        with self.assertRaises(ValidationError):
            NarrativeAnalysisResult(
                central_theme="Tema",
                historical_or_factual_context="Contexto",
                narrative_objective="Objetivo",
                narrative_progression=["Início", "Meio", "Fim"],
                scene_analyses=[self._make_scene_analysis(1)],
                total_scenes=0,
            )

    def test_total_scenes_empty_list(self):
        from pydantic import ValidationError
        res = NarrativeAnalysisResult(
            central_theme="Tema",
            historical_or_factual_context="Contexto",
            narrative_objective="Objetivo",
            narrative_progression=["Início", "Meio", "Fim"],
            scene_analyses=[],
            total_scenes=0,
        )
        self.assertEqual(res.total_scenes, 0)
        with self.assertRaises(ValidationError):
            NarrativeAnalysisResult(
                central_theme="Tema",
                historical_or_factual_context="Contexto",
                narrative_objective="Objetivo",
                narrative_progression=["Início", "Meio", "Fim"],
                scene_analyses=[],
                total_scenes=2,
            )

    def test_total_scenes_omitted_auto_calculates(self):
        res = NarrativeAnalysisResult(
            central_theme="Tema",
            historical_or_factual_context="Contexto",
            narrative_objective="Objetivo",
            narrative_progression=["Início", "Meio", "Fim"],
            scene_analyses=[self._make_scene_analysis(1), self._make_scene_analysis(2)],
        )
        self.assertEqual(res.total_scenes, 2)

    def test_to_canonical_scenes_preserves_zero_id_zero_timestamp_and_roundtrip(self):
        from shared.models import Scene

        orig_dict = {
            "id": 0,
            "narration": "Cena zero",
            "duration_s": 7.5,
            "estimated_duration_s": 7.5,
            "timestamp": 0,
            "scene_type": "hook",
            "narrative_purpose": "hook",
            "emotion": "shock",
            "custom_meta": "preservado",
        }
        direction = SceneVisualDirection(
            scene_id=0,
            narration="Cena zero",
            what_viewer_sees="Plano aberto de cidade noturna",
            dominant_element="Arranha-céus iluminados",
            b_roll_visual="Plano aberto",
            b_roll_query="open shot city",
            continuity_anchor="anchor_0",
        )
        vdr = VisualDirectionResult(
            visual_bible=VisualBible(
                visual_language=VisualLanguage.DOCUMENTARY_REALISTIC,
                dominant_style="documentary_cinematic",
                color_palette=["#0B0C10", "#1F2833"],
                lighting_mood="dark_cinematic",
                pacing_rhythm="slow_burn",
            ),
            scene_directions=[direction],
        )
        canonical = vdr.to_canonical_scenes(original_scenes=[orig_dict])
        self.assertEqual(len(canonical), 1)
        s0 = canonical[0]
        self.assertIsInstance(s0, Scene)
        self.assertEqual(s0.id, 0)
        self.assertEqual(s0.duration_s, 7.5)
        self.assertEqual(s0.timestamp, "0")
        self.assertEqual(s0.continuity_anchor, "anchor_0")
        self.assertEqual(getattr(s0, "estimated_duration_s", None), 7.5)
        self.assertEqual(getattr(s0, "custom_meta", None), "preservado")

        # Round-trip com objeto Scene
        canonical_rt = vdr.to_canonical_scenes(original_scenes=canonical)
        self.assertEqual(canonical_rt[0].id, 0)
        self.assertEqual(canonical_rt[0].duration_s, 7.5)
        self.assertEqual(canonical_rt[0].timestamp, "0")
        self.assertEqual(getattr(canonical_rt[0], "custom_meta", None), "preservado")

    def test_to_canonical_scenes_rejects_invalid_duration(self):
        direction = SceneVisualDirection(
            scene_id=1,
            narration="Cena inválida",
            what_viewer_sees="Plano fechado de documento",
            dominant_element="Documento oficial",
            b_roll_visual="Plano",
            b_roll_query="document close up",
        )
        vdr = VisualDirectionResult(
            visual_bible=VisualBible(
                visual_language=VisualLanguage.DOCUMENTARY_REALISTIC,
                dominant_style="documentary_cinematic",
                color_palette=["#0B0C10", "#1F2833"],
                lighting_mood="dark_cinematic",
                pacing_rhythm="slow_burn",
            ),
            scene_directions=[direction],
        )
        with self.assertRaises(ValueError):
            vdr.to_canonical_scenes(original_scenes=[{"id": 1, "duration_s": "not-a-number"}])
        with self.assertRaises(ValueError):
            vdr.to_canonical_scenes(original_scenes=[{"id": 1, "duration_s": 0}])

    def test_sanitize_secret_comprehensive_positive_and_negative(self):
        from agents.video_director.security import sanitize_secret

        cases = [
            ("Authorization: Bearer eyJhbGciOi+/=123456", "eyJhbGciOi+/=123456"),
            ("Authorization: Basic dXNlcjpwYXNzMTIz", "dXNlcjpwYXNzMTIz"),
            ("x-api-key: my-secret-header-key", "my-secret-header-key"),
            ("OpenAI sk-proj-abcdef1234567890xyz", "sk-proj-abcdef1234567890xyz"),
            ("DeepSeek sk-1234567890abcdef1234", "sk-1234567890abcdef1234"),
            ("Gemini AIzaSyDummyKey12345678901234567890", "AIzaSyDummyKey12345678901234567890"),
            ("https://api.example.com/v1?key=SECRET123&token=TOK456", "SECRET123"),
            ("https://admin:p@ssword!123@db.internal:5432/prod", "p@ssword!123"),
            ('PASSWORD="my;secret,pass 123" api_key=abc_987', "my;secret,pass 123"),
        ]
        for raw, secret in cases:
            sanitized = sanitize_secret(raw)
            self.assertNotIn(secret, sanitized, f"Secret leaked in: {sanitized}")

        # Negative test: diagnostic info without secrets must be preserved
        diag = "HTTP 504 Gateway Timeout on endpoint /v1/models/gemini-2.5-flash after 30.0s (attempt 2)"
        self.assertEqual(sanitize_secret(diag), diag)

    def test_timeout_propagation_strict_llm_client_and_orchestrator(self):
        from unittest.mock import patch
        from shared.llm_client import LLMClient, LLMResponse
        from shared.ai_orchestrator import AIOrchestrator

        client = LLMClient.__new__(LLMClient)
        client._gemini_client = True
        client._deepseek_client = object()
        client._openai_client = object()
        client._gemini_api_key = "fake"

        seen_timeouts = []

        def strict_gemini(model_name, system_prompt, user_prompt, temperature, max_tokens, json_mode, timeout=None):
            seen_timeouts.append(("gemini", timeout))
            raise TimeoutError("gemini timed out")

        def strict_openai(model_name, system_prompt, user_prompt, temperature, max_tokens, json_mode, client=None, timeout=None):
            seen_timeouts.append(("openai_compat", timeout))
            return LLMResponse(text="ok", model_used=model_name, tokens_used=1)

        client._call_gemini = strict_gemini
        client._call_openai = strict_openai

        with patch("shared.llm_client.MOCK_LLM", False):
            resp = client.complete("sys", "usr", timeout=12.5)
        self.assertEqual(resp.text, "ok")
        self.assertEqual(len(seen_timeouts), 2)
        self.assertEqual(seen_timeouts[0][0], "gemini")
        self.assertEqual(seen_timeouts[1][0], "openai_compat")
        self.assertLessEqual(seen_timeouts[0][1], 12.5)
        self.assertGreater(seen_timeouts[1][1], 0.0)
        self.assertLessEqual(seen_timeouts[1][1], seen_timeouts[0][1])

        # Orchestrator strict timeout propagation
        orch = AIOrchestrator.__new__(AIOrchestrator)
        orch.gemini_key = "fake"
        orch.primary_model = "gemini-2.5-flash"
        orch.groq_client = None
        orch.openai_client = None
        orch.or_client = None
        orch.deepseek_client = None
        orch.mistral_client = None
        orch.ollama_url = None
        orch.last_provider_used = None
        orch.last_model_used = None
        orch.providers_status = {"gemini": {"active": True, "cooldown_until": None}}

        orch_timeouts = []

        def strict_orch_gemini(model, system, user, json_mode, max_tokens, timeout=None):
            orch_timeouts.append(timeout)
            return LLMResponse(text="ok", model_used=model, tokens_used=1)

        orch._call_gemini = strict_orch_gemini
        import uuid
        unique_usr = f"usr-{uuid.uuid4()}"
        orch.chat_completion("sys", unique_usr, timeout=15.0)
        self.assertEqual(orch_timeouts, [15.0])

    def test_timeout_propagation_agents_and_fallbacks_strict(self):
        """Garante propagação estrita de timeout em NarrativeAnalyzer, VisualDirector, AIOrchestrator e fallback."""
        import uuid
        from unittest.mock import MagicMock
        from shared.llm_client import LLMResponse
        from shared.ai_orchestrator import AIOrchestrator
        from agents.video_director.narrative_analyzer import NarrativeAnalyzer
        from agents.video_director.visual_director import VisualDirector

        # 1. NarrativeAnalyzer transmite timeout 15.0
        na_timeouts = []

        class StrictOrchNA:
            def chat_completion(self, system_prompt, user_prompt, json_mode=False, max_tokens=8192, task_type="general", timeout=None):
                na_timeouts.append(timeout)
                return LLMResponse(
                    text='{"story_arc": {"hook": "h", "escalation": "e", "climax": "c", "resolution": "r"}, "beats": [], "pacing_notes": "p"}',
                    model_used="mock",
                    tokens_used=1,
                )

        na = NarrativeAnalyzer(llm_client=StrictOrchNA(), timeout=15.0)
        try:
            na.analyze(script_input={"title": "Titulo", "scenes": [{"scene_index": 0, "narration": "Texto de teste"}]})
        except Exception:
            pass
        self.assertEqual(na_timeouts, [15.0])

        # 2. VisualDirector transmite timeout 15.0
        vd_timeouts = []

        class StrictOrchVD:
            def chat_completion(self, system_prompt, user_prompt, json_mode=False, max_tokens=8192, task_type="general", timeout=None):
                vd_timeouts.append(timeout)
                return LLMResponse(
                    text='{"visual_bible": {"visual_language": "investigative", "dominant_style": "noir_documentary", "color_palette": ["#101820"], "lighting_mood": "low_key_chiaroscuro", "pacing_rhythm": "deliberate_tension"}, "scene_directions": []}',
                    model_used="mock",
                    tokens_used=1,
                )

        from agents.video_director.models import NarrativeAnalysisResult, SceneNarrativeInterpretation

        vd = VisualDirector(llm_client=StrictOrchVD(), timeout=15.0)
        dummy_na = NarrativeAnalysisResult(
            central_theme="Tema",
            historical_or_factual_context="Contexto",
            narrative_objective="Objetivo",
            narrative_progression=["Abertura", "Desenvolvimento", "Conclusão"],
            total_scenes=1,
            scene_analyses=[
                SceneNarrativeInterpretation(
                    scene_id=0,
                    original_narration="Olá",
                    scene_intent="Abertura",
                )
            ],
        )
        try:
            vd.direct(narrative_analysis=dummy_na)
        except Exception:
            pass
        self.assertEqual(vd_timeouts, [15.0])

        # 3, 6, 7, 8, 9. AIOrchestrator fallback mantém timeout e preserva causa original
        orch = AIOrchestrator.__new__(AIOrchestrator)
        orch.gemini_key = "fake"
        orch.primary_model = "gemini-2.5-flash"
        orch.groq_client = object()
        orch.groq_model = "llama-3.3-70b-versatile"
        orch.openai_client = None
        orch.or_client = None
        orch.deepseek_client = None
        orch.mistral_client = None
        orch.ollama_url = None
        orch.last_provider_used = None
        orch.last_model_used = None
        orch.providers_status = {
            "gemini": {"active": True, "cooldown_until": None},
            "groq": {"active": True, "cooldown_until": None},
        }

        fallback_timeouts = []

        def failing_gemini(model, system, user, json_mode, max_tokens, timeout=None):
            fallback_timeouts.append(("gemini", timeout))
            raise TimeoutError("gemini timeout")

        def strict_groq(client, model, provider, system, user, json_mode, max_tokens, timeout=None):
            fallback_timeouts.append((provider, timeout))
            return LLMResponse(text="ok", model_used=model, tokens_used=1)

        orch._call_gemini = failing_gemini
        orch._call_openai_compatible = strict_groq
        resp = orch.chat_completion("sys", f"usr-{uuid.uuid4()}", timeout=15.0)
        self.assertEqual(resp.text, "ok")
        self.assertEqual(fallback_timeouts, [("gemini", 15.0), ("Groq", 15.0)])


class TestVisualBibleValidation(unittest.TestCase):
    """Testes estritos de validação da Bíblia Visual (Sprint 2.2)."""

    def _valid_bible_payload(self):
        return {
            "visual_language": "investigative",
            "dominant_style": "noir_documentary",
            "color_palette": ["#101820", "#FEE715", "#2C3531"],
            "lighting_mood": "low_key_chiaroscuro",
            "pacing_rhythm": "deliberate_tension",
        }

    def test_visual_bible_complete_valid(self):
        """1. Bíblia Visual completa é aceita."""
        from agents.video_director.models import VisualBible
        bible = VisualBible(**self._valid_bible_payload())
        self.assertEqual(bible.visual_language, "investigative")
        self.assertEqual(bible.dominant_style, "noir_documentary")
        self.assertEqual(bible.color_palette, ["#101820", "#FEE715", "#2C3531"])
        self.assertEqual(bible.lighting_mood, "low_key_chiaroscuro")
        self.assertEqual(bible.pacing_rhythm, "deliberate_tension")

    def test_visual_bible_missing_in_llm_response(self):
        """2. Bíblia Visual ausente na resposta do LLM é rejeitada."""
        from pydantic import ValidationError
        from agents.video_director.models import VisualDirectionResult
        with self.assertRaises(ValidationError):
            VisualDirectionResult(scene_directions=[])

    def test_visual_bible_empty_dict_rejected(self):
        """3. Bíblia Visual vazia ({}) é rejeitada e não usa defaults."""
        from pydantic import ValidationError
        from agents.video_director.models import VisualBible
        with self.assertRaises(ValidationError):
            VisualBible(**{})

    def test_visual_bible_missing_essential_field_rejected(self):
        """4. Bíblia Visual com campo essencial ausente é rejeitada."""
        from pydantic import ValidationError
        from agents.video_director.models import VisualBible
        for field in ("visual_language", "dominant_style", "color_palette", "lighting_mood", "pacing_rhythm"):
            payload = self._valid_bible_payload()
            del payload[field]
            with self.assertRaises(ValidationError, msg=f"Should fail when {field} is missing"):
                VisualBible(**payload)

    def test_visual_bible_empty_palette_rejected(self):
        """5. Paleta vazia é rejeitada."""
        from pydantic import ValidationError
        from agents.video_director.models import VisualBible
        payload = self._valid_bible_payload()
        payload["color_palette"] = []
        with self.assertRaises(ValidationError):
            VisualBible(**payload)

    def test_visual_bible_invalid_enum_rejected(self):
        """6. Enum inválido em visual_language é rejeitado."""
        from pydantic import ValidationError
        from agents.video_director.models import VisualBible
        payload = self._valid_bible_payload()
        payload["visual_language"] = "cartoon_anime_invalid"
        with self.assertRaises(ValidationError):
            VisualBible(**payload)

    def test_visual_bible_empty_string_in_essential_field_rejected(self):
        """7. Campo essencial contendo string vazia ou apenas espaços é rejeitado."""
        from pydantic import ValidationError
        from agents.video_director.models import VisualBible
        for field in ("dominant_style", "lighting_mood", "pacing_rhythm"):
            for bad_val in ("", "   "):
                payload = self._valid_bible_payload()
                payload[field] = bad_val
                with self.assertRaises(ValidationError, msg=f"Should fail when {field}={bad_val!r}"):
                    VisualBible(**payload)

    def test_visual_bible_valid_values_preserved_without_substitution(self):
        """8. Valores válidos são preservados exatamente sem substituição por defaults."""
        from agents.video_director.models import VisualBible
        custom = {
            "visual_language": "historical",
            "dominant_style": "archival_16mm_grain",
            "color_palette": ["#1A1A1D", "#4E4E50", "#C3073F"],
            "lighting_mood": "tungsten_archival_lamp",
            "pacing_rhythm": "measured_chronicle",
        }
        bible = VisualBible(**custom)
        self.assertEqual(bible.visual_language, "historical")
        self.assertEqual(bible.dominant_style, "archival_16mm_grain")
        self.assertEqual(bible.color_palette, ["#1A1A1D", "#4E4E50", "#C3073F"])
        self.assertEqual(bible.lighting_mood, "tungsten_archival_lamp")
        self.assertEqual(bible.pacing_rhythm, "measured_chronicle")
