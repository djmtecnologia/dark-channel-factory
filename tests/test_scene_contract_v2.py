"""
Testes Automatizados do Contrato Canônico V2 e Retrocompatibilidade — Sprint 2.1
================================================================================
Testa a conformidade do modelo Scene (shared/models.py) e do normalizador
(shared/scene_normalizer.py) com a especificação da Fase 2 (AI Video Director).

Casos de Teste Obrigatórios:
1. Entrada exclusivamente com campos canônicos.
2. Entrada exclusivamente com campos legados.
3. Entrada com mistura de campos canônicos e legados.
4. Reconciliação bidirecional de id e scene_id.
5. Reconciliação de narration, text e content (e rejeição de narração vazia).
6. Reconciliação de duration_s e estimated_duration_s (coerção float >= 1.0).
7. Geração determinística de b_roll_query curta (2 a 5 palavras) a partir de b_roll_visual.
8. Preservação independente de b_roll_visual e b_roll_query quando ambos existem.
9. Geração de timestamp sequencial contínuo quando ausente.
10. Validação de enums (incluindo CameraMovement.IMPACT_MICRO_SHAKE) e defaults seguros.
11. Serialização canônica V2 e compatibilidade de entrada/acesso legado.
12. Imutabilidade estrita (ausência de mutação inesperada nos dicionários originais).
13. Preservação integral de metadados válidos e dados adicionais durante a normalização.
"""
from __future__ import annotations

import copy
import unittest
from typing import Any, Dict

import pytest
from pydantic import ValidationError

from shared.models import (
    AssetMetadata,
    AssetSource,
    CameraFraming,
    CameraMovement,
    Emotion,
    NarrativePurpose,
    Scene,
    SceneType,
    ShotType,
    VisualProductionType,
    VisualType,
)
from shared.scene_normalizer import (
    extract_concise_broll_query,
    normalize_broll_query,
    normalize_scene,
    normalize_scene_v2,
    normalize_scenes,
    normalize_scenes_v2,
)


class TestSceneContractV2(unittest.TestCase):
    """Suíte de validação formal do Contrato Canônico V2 e retrocompatibilidade."""

    def test_01_canonical_fields_only(self):
        """1. Entrada exclusivamente com campos canônicos V2 deve ser aceita e validada perfeitamente."""
        canonical_input = {
            "id": 1,
            "narration": "In 1969, humanity achieved its greatest milestone by landing on the Moon.",
            "duration_s": 4.5,
            "timestamp": "0:00-0:04",
            "scene_type": "solution",
            "narrative_purpose": "historical_evidence",
            "emotion": "shock",
            "b_roll_visual": "NASA Mission Control Center in Houston July 1969 engineers with headsets monochrome CRT monitors",
            "b_roll_query": "NASA Apollo 11 mission control",
            "visual_production_type": "stock_footage",
            "camera_framing": "wide",
            "camera_movement": "slow_push_in",
            "lighting_mood": "dark_cinematic",
            "transition_in": "direct_cut",
            "transition_out": "crossfade_slow",
            "continuity_anchor": "apollo_11_launch",
            "sfx_cue": "telemetry_room_hum",
            "on_screen_text": "Apollo 11 Mission",
            "visual_cues": [{"type": "zoom", "target": "screen"}],
            "estimated_cost_tier": 1,
        }

        scene = Scene(**canonical_input)
        self.assertEqual(scene.id, 1)
        self.assertEqual(scene.narration, canonical_input["narration"])
        self.assertEqual(scene.duration_s, 4.5)
        self.assertEqual(scene.narrative_purpose, NarrativePurpose.HISTORICAL_EVIDENCE)
        self.assertEqual(scene.camera_movement, CameraMovement.SLOW_PUSH_IN)
        self.assertEqual(scene.camera_framing, CameraFraming.WIDE)
        self.assertEqual(scene.b_roll_visual, canonical_input["b_roll_visual"])
        self.assertEqual(scene.b_roll_query, canonical_input["b_roll_query"])

        # Normalizador também deve processar preservando todos os campos
        norm = normalize_scene_v2(canonical_input)
        self.assertEqual(norm["id"], 1)
        self.assertEqual(norm["narration"], canonical_input["narration"])
        self.assertEqual(norm["narrative_purpose"], "historical_evidence")
        self.assertEqual(norm["camera_movement"], "slow_push_in")

    def test_02_legacy_fields_only(self):
        """2. Entrada exclusivamente com campos legados da Fase 1 deve ser mapeada com sucesso."""
        legacy_input = {
            "scene_id": 10,
            "text": "The entire digital financial grid operates on high frequency algorithms.",
            "estimated_duration_s": 6.0,
            "visual_description": "macro shot of gold bullion bars inside bank vault",
            "query": "gold bars bank vault",
            "type": "problem",
            "micro_cues": [{"action": "flash"}],
            "overlay_text": "GOLD VAULT",
            "tier": 2,
        }

        scene = Scene(**legacy_input)
        # Campos canônicos preenchidos via aliases e reconciliação
        self.assertEqual(scene.id, 10)
        self.assertEqual(scene.narration, legacy_input["text"])
        self.assertEqual(scene.duration_s, 6.0)
        self.assertEqual(scene.b_roll_visual, legacy_input["visual_description"])
        self.assertEqual(scene.b_roll_query, legacy_input["query"])
        self.assertEqual(scene.scene_type, SceneType.PROBLEM)

        # Compatibilidade com consumidores legados
        self.assertEqual(scene.scene_id, 10)
        self.assertEqual(scene.text, legacy_input["text"])
        self.assertEqual(scene.estimated_duration_s, 6.0)

    def test_03_mixed_canonical_and_legacy_fields(self):
        """3. Entrada com mistura de campos canônicos e legados deve reconciliar sem conflitos."""
        mixed_input = {
            "scene_id": 5,  # legado
            "narration": "Scientists detected unprecedented gravitational waves.",  # canônico
            "text": "Old text that should be synchronized",  # legado
            "duration_s": 7.5,  # canônico
            "visual_prompt": "laser interferometer gravitational observatory sensors",  # legado
            "b_roll_query": "gravitational wave detector",  # canônico
            "camera_framing": "medium",  # canônico
        }

        scene = Scene(**mixed_input)
        self.assertEqual(scene.id, 5)
        self.assertEqual(scene.narration, "Scientists detected unprecedented gravitational waves.")
        self.assertEqual(scene.text, "Scientists detected unprecedented gravitational waves.")
        self.assertEqual(scene.duration_s, 7.5)
        self.assertEqual(scene.b_roll_visual, "laser interferometer gravitational observatory sensors")
        self.assertEqual(scene.b_roll_query, "gravitational wave detector")
        self.assertEqual(scene.camera_framing, CameraFraming.MEDIUM)

    def test_04_id_and_scene_id_reconciliation(self):
        """4. Reconciliação id / scene_id: se um é fornecido, ambos são disponibilizados."""
        # Apenas scene_id fornecido
        s1 = Scene(
            scene_id=42,
            narration="Test scene identification.",
            duration_s=5.0,
            b_roll_visual="visual prompt",
            b_roll_query="visual query",
        )
        self.assertEqual(s1.id, 42)
        self.assertEqual(s1.scene_id, 42)

        # Apenas id fornecido
        s2 = Scene(
            id=7,
            narration="Test scene identification 2.",
            duration_s=5.0,
            b_roll_visual="visual prompt",
            b_roll_query="visual query",
        )
        self.assertEqual(s2.id, 7)
        self.assertEqual(s2.scene_id, 7)

        # No normalizador
        n1 = normalize_scene({"scene_id": 99, "narration": "Test"})
        self.assertEqual(n1["id"], 99)
        self.assertEqual(n1["scene_id"], 99)

    def test_05_narration_text_content_and_rejection_of_empty(self):
        """5. Reconciliação narration / text / content e rejeição estrita de narração vazia."""
        # content legado mapeia para narration
        s_content = Scene(
            id=1,
            content="Extracted from content key.",
            duration_s=5.0,
            b_roll_visual="visual",
            b_roll_query="query",
        )
        self.assertEqual(s_content.narration, "Extracted from content key.")
        self.assertEqual(s_content.text, "Extracted from content key.")

        # Rejeição de narração vazia: não permite validação com string vazia ou whitespace
        with self.assertRaises(ValidationError):
            Scene(
                id=1,
                narration="",
                duration_s=5.0,
                b_roll_visual="visual",
                b_roll_query="query",
            )

        with self.assertRaises(ValidationError):
            Scene(
                id=1,
                narration="   \t\n  ",
                duration_s=5.0,
                b_roll_visual="visual",
                b_roll_query="query",
            )

        with self.assertRaises(ValidationError):
            Scene(
                id=1,
                text="   ",
                duration_s=5.0,
                b_roll_visual="visual",
                b_roll_query="query",
            )

    def test_06_duration_reconciliation_and_coercion(self):
        """6. Reconciliação duration_s / estimated_duration_s com coerção para float >= 1.0."""
        # Fornecido string coercível
        scene = Scene(
            id=1,
            narration="Testing duration coercion.",
            estimated_duration_s="8.5",
            b_roll_visual="visual",
            b_roll_query="query",
        )
        self.assertEqual(scene.duration_s, 8.5)
        self.assertEqual(scene.estimated_duration_s, 8.5)

        # Duração inferior a 1.0 é saturada para no mínimo 1.0
        scene_low = Scene(
            id=1,
            narration="Testing minimal duration threshold.",
            duration_s=0.2,
            b_roll_visual="visual",
            b_roll_query="query",
        )
        self.assertGreaterEqual(scene_low.duration_s, 1.0)

        # Normalizador também preserva float e saturação >= 1.0
        norm = normalize_scene({"narration": "Test", "duration": "12.3"})
        self.assertEqual(norm["duration_s"], 12.3)
        self.assertEqual(norm["estimated_duration_s"], 12.3)

    def test_07_concise_query_generation_from_b_roll_visual(self):
        """7. Geração determinística de query concisa (2 a 5 palavras) a partir de b_roll_visual."""
        detailed_visual = (
            "Cinematic extreme close-up of Apollo lunar boot descending the final rung of the "
            "Eagle lander ladder deeply textured pressurized tread touching virgin lunar dust"
        )

        # Extração direta pelo algoritmo
        concise = extract_concise_broll_query(detailed_visual, min_words=2, max_words=5)
        word_count = len(concise.split())
        self.assertGreaterEqual(word_count, 2)
        self.assertLessEqual(word_count, 5)
        # Não deve conter prefixos cinemáticos
        self.assertNotIn("cinematic", concise.lower())
        self.assertNotIn("extreme", concise.lower())

        # Através do modelo Scene (quando apenas b_roll_visual é fornecido)
        scene = Scene(
            id=1,
            narration="Neil Armstrong descends to the surface.",
            duration_s=5.0,
            b_roll_visual=detailed_visual,
        )
        self.assertEqual(scene.b_roll_visual, detailed_visual)
        q_count = len(scene.b_roll_query.split())
        self.assertGreaterEqual(q_count, 2)
        self.assertLessEqual(q_count, 5)
        self.assertNotEqual(scene.b_roll_query, detailed_visual)

        # Através de normalize_scene_v2
        norm = normalize_scene_v2({
            "narration": "Descent to the surface.",
            "b_roll_visual": "NASA Mission Control Center in Houston July 1969 engineers with headsets monochrome CRT monitors",
        })
        self.assertEqual(norm["b_roll_visual"], "NASA Mission Control Center in Houston July 1969 engineers with headsets monochrome CRT monitors")
        norm_words = len(norm["b_roll_query"].split())
        self.assertGreaterEqual(norm_words, 2)
        self.assertLessEqual(norm_words, 5)

    def test_08_independent_preservation_when_both_exist(self):
        """8. Preservação independente de b_roll_visual e b_roll_query quando ambos existem."""
        custom_visual = "Vintage 16mm grainy archive of Saturn V rocket launching with fiery exhaust"
        custom_query = "Saturn V rocket launch 1969"

        scene = Scene(
            id=1,
            narration="The engines ignited with deafening roar.",
            duration_s=5.0,
            b_roll_visual=custom_visual,
            b_roll_query=custom_query,
        )
        # Nenhum dos campos deve sobrescrever o outro
        self.assertEqual(scene.b_roll_visual, custom_visual)
        self.assertEqual(scene.b_roll_query, custom_query)

        norm = normalize_scene_v2({
            "narration": "Rocket launch.",
            "b_roll_visual": custom_visual,
            "b_roll_query": custom_query,
        })
        self.assertEqual(norm["b_roll_visual"], custom_visual)
        self.assertEqual(norm["b_roll_query"], custom_query)

    def test_09_sequential_timestamp_generation(self):
        """9. Geração de timestamp sequencial contínuo a partir das durações das cenas."""
        scenes_data = [
            {"narration": "First scene introductory hook.", "duration_s": 4.0},
            {"narration": "Second scene contextual evidence.", "duration_s": 6.5},
            {"narration": "Third scene technical climax.", "duration_s": 5.5},
        ]

        normalized_scenes = normalize_scenes_v2(scenes_data)
        self.assertEqual(len(normalized_scenes), 3)

        # Cena 0: 0.0s a 4.0s -> "0:00-0:04"
        self.assertEqual(normalized_scenes[0]["timestamp"], "0:00-0:04")
        # Cena 1: 4.0s a 10.5s -> "0:04-0:10"
        self.assertEqual(normalized_scenes[1]["timestamp"], "0:04-0:10")
        # Cena 2: 10.5s a 16.0s -> "0:10-0:16"
        self.assertEqual(normalized_scenes[2]["timestamp"], "0:10-0:16")

    def test_10_enums_validation_and_defaults(self):
        """10. Validação de todos os enums oficiais (incluindo impact_micro_shake) e defaults seguros."""
        # CameraMovement enum deve conter impact_micro_shake
        self.assertEqual(CameraMovement.IMPACT_MICRO_SHAKE.value, "impact_micro_shake")

        # Cena criada com defaults mínimos deve preencher enums seguros
        scene = Scene(
            id=1,
            narration="Testing default audiovisual parameters.",
            b_roll_visual="visual description",
            b_roll_query="visual query",
        )
        self.assertEqual(scene.camera_framing, CameraFraming.WIDE)
        self.assertEqual(scene.camera_movement, CameraMovement.SLOW_PUSH_IN)
        self.assertEqual(scene.narrative_purpose, NarrativePurpose.CONTEXT)
        self.assertEqual(scene.visual_production_type, VisualProductionType.AUTO)
        self.assertEqual(scene.scene_type, SceneType.SOLUTION)
        self.assertEqual(scene.emotion, Emotion.CURIOSITY)
        self.assertEqual(scene.transition_in, "direct_cut")
        self.assertEqual(scene.transition_out, "crossfade_slow")
        self.assertEqual(scene.lighting_mood, "dark_cinematic")
        self.assertEqual(scene.sfx_cue, "none")
        self.assertEqual(scene.estimated_cost_tier, 1)

        # Cena instanciada com impact_micro_shake
        scene_shake = Scene(
            id=2,
            narration="Lunar boot touches surface.",
            b_roll_visual="lunar boot dust contact",
            b_roll_query="lunar boot dust",
            camera_movement=CameraMovement.IMPACT_MICRO_SHAKE,
        )
        self.assertEqual(scene_shake.camera_movement, "impact_micro_shake")

        # Entrada insensível a maiúsculas/minúsculas para enums
        scene_upper = Scene(
            id=3,
            narration="Uppercase enum test.",
            b_roll_visual="visual",
            b_roll_query="query",
            camera_framing="CLOSE_UP",
            camera_movement="IMPACT_MICRO_SHAKE",
            narrative_purpose="REVELATION",
        )
        self.assertEqual(scene_upper.camera_framing, CameraFraming.CLOSE_UP)
        self.assertEqual(scene_upper.camera_movement, CameraMovement.IMPACT_MICRO_SHAKE)
        self.assertEqual(scene_upper.narrative_purpose, NarrativePurpose.REVELATION)

    def test_11_canonical_serialization_and_legacy_access(self):
        """11. Serialização canônica V2 (model_dump) e compatibilidade com consumidores legados."""
        scene = Scene(
            id=1,
            narration="Canonically serialized scene content.",
            duration_s=6.0,
            b_roll_visual="cinematic scene prompt",
            b_roll_query="scene prompt",
            camera_framing=CameraFraming.MACRO,
        )

        dump = scene.model_dump()
        # Chaves canônicas devem estar presentes
        self.assertIn("id", dump)
        self.assertIn("narration", dump)
        self.assertIn("duration_s", dump)
        self.assertIn("b_roll_visual", dump)
        self.assertIn("b_roll_query", dump)
        self.assertIn("camera_framing", dump)

        # Chave de compatibilidade 'text' deve estar espelhada
        self.assertIn("text", dump)
        self.assertEqual(dump["text"], scene.narration)

        # Consumidores que acessam por atributo legado devem funcionar
        self.assertEqual(scene.scene_id, 1)
        self.assertEqual(scene.estimated_duration_s, 6.0)
        self.assertEqual(scene.text, "Canonically serialized scene content.")

    def test_12_immutability_of_input_dictionaries(self):
        """12. Ausência de mutação inesperada nos dicionários originais fornecidos à normalização."""
        original_dict: Dict[str, Any] = {
            "id": 1,
            "narration": "Original narration text.",
            "duration_s": 5.0,
            "b_roll_visual": "Original detailed visual prompt",
            "visual_cues": [{"cue_id": 1, "description": "micro action"}],
            "custom_metadata": {"author": "AI Writer", "score": 95},
        }

        # Cópia profunda para verificação de igualdade estrita
        snapshot_before = copy.deepcopy(original_dict)

        # Executa normalização V2 e instanciação do modelo Scene
        norm = normalize_scene_v2(original_dict)
        _ = Scene(**original_dict)

        # Altera intencionalmente a saída normalizada
        norm["narration"] = "MUTATED NARRATION"
        norm["visual_cues"].append({"cue_id": 999})
        norm["custom_metadata"]["mutated"] = True

        # O dicionário original NÃO pode ter sido alterado de forma alguma
        self.assertEqual(original_dict, snapshot_before)
        self.assertEqual(original_dict["narration"], "Original narration text.")
        self.assertEqual(len(original_dict["visual_cues"]), 1)
        self.assertNotIn("mutated", original_dict["custom_metadata"])

    def test_13_preservation_of_all_valid_and_custom_metadata(self):
        """13. Preservação de todos os dados válidos, metadados de licença e chaves customizadas."""
        asset_meta = AssetMetadata(
            provider="nasa_archive",
            source_id_or_url="https://images.nasa.gov/details/AS11-40-5874",
            license_type="public_domain_gov",
            license_proof_ref="https://www.nasa.gov/multimedia/guidelines/index.html",
            attribution_required=False,
            local_asset_path="assets_cache/nasa_apollo11_boot.jpg",
            sha256_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            commercial_use_approved=True,
            editorial_only=False,
        )

        input_data = {
            "id": 1,
            "narration": "Authentic archival footage from NASA Johnson Space Center.",
            "duration_s": 5.0,
            "b_roll_visual": "Apollo 11 lunar boot touching lunar dust",
            "b_roll_query": "Apollo 11 lunar boot",
            "asset_metadata": asset_meta,
            "client_tracking_id": "ABC-12345",
            "custom_render_flag": True,
        }

        scene = Scene(**input_data)
        self.assertIsNotNone(scene.asset_metadata)
        self.assertEqual(scene.asset_metadata.provider, "nasa_archive")
        self.assertEqual(scene.asset_metadata.license_type, "public_domain_gov")
        self.assertTrue(scene.asset_metadata.commercial_use_approved)
        # Campos extras preservados pelo extra="allow"
        self.assertEqual(getattr(scene, "client_tracking_id", None), "ABC-12345")

        # Normalizador também preserva metadados e chaves customizadas
        norm = normalize_scene_v2(input_data)
        self.assertIsNotNone(norm["asset_metadata"])
        self.assertEqual(norm["asset_metadata"]["provider"], "nasa_archive")
        self.assertEqual(norm["client_tracking_id"], "ABC-12345")
        self.assertTrue(norm["custom_render_flag"])

    def test_14_conflicting_identifiers_rejection(self):
        """14. Rejeição explícita de identificadores conflitantes e id negativo."""
        # id != scene_id em Scene deve levantar erro
        with self.assertRaises(ValidationError):
            Scene(
                id=1,
                scene_id=2,
                narration="Conflicting IDs test.",
                b_roll_visual="visual",
                b_roll_query="query",
            )

        # id != scene_id em normalize_scene deve levantar ValueError
        with self.assertRaises(ValueError):
            normalize_scene({
                "id": 1,
                "scene_id": 2,
                "narration": "Conflicting IDs test in normalizer.",
            })

        # id negativo deve ser rejeitado (aceita somente id >= 0)
        with self.assertRaises(ValidationError):
            Scene(
                id=-1,
                narration="Negative ID test.",
                b_roll_visual="visual",
                b_roll_query="query",
            )

        with self.assertRaises(ValueError):
            normalize_scene({
                "id": -5,
                "narration": "Negative ID test in normalizer.",
            })

        # id=0 e scene_id=0 são aceitos para compatibilidade com Cena 0 (Hook) da Fase 1
        s_zero = Scene(
            id=0,
            narration="Hook scene zero test.",
            b_roll_visual="visual",
            b_roll_query="query",
        )
        self.assertEqual(s_zero.id, 0)
        self.assertEqual(s_zero.scene_id, 0)

        n_zero = normalize_scene({
            "id": 0,
            "narration": "Hook scene zero test in normalizer.",
        })
        self.assertEqual(n_zero["id"], 0)
        self.assertEqual(n_zero["scene_id"], 0)

    def test_15_narration_empty_rejection_in_normalizer(self):
        """15. Rejeição estrita de narração vazia no normalizador (não mascarar com string vazia)."""
        with self.assertRaises(ValueError):
            normalize_scene({"id": 1, "narration": ""})

        with self.assertRaises(ValueError):
            normalize_scene({"id": 1, "narration": "   \n\t  "})

        with self.assertRaises(ValueError):
            normalize_scene({"id": 1, "text": "   "})

        with self.assertRaises(ValueError):
            normalize_scene({"id": 1})

    def test_16_duration_validation_rejection_negative_zero_nan_inf_and_conflicts(self):
        """16. Duração: rejeição estrita de zero, negativos, NaN, infinitos e conflito entre aliases."""
        # Zero rejeitado
        with self.assertRaises(ValidationError):
            Scene(id=1, narration="Test", duration_s=0.0, b_roll_visual="vis", b_roll_query="q")

        with self.assertRaises(ValueError):
            normalize_scene({"id": 1, "narration": "Test", "duration_s": 0.0})

        # Negativo rejeitado
        with self.assertRaises(ValidationError):
            Scene(id=1, narration="Test", duration_s=-4.0, b_roll_visual="vis", b_roll_query="q")

        with self.assertRaises(ValueError):
            normalize_scene({"id": 1, "narration": "Test", "duration_s": -2.5})

        # NaN e Inf rejeitados
        import math
        with self.assertRaises(ValidationError):
            Scene(id=1, narration="Test", duration_s=float("nan"), b_roll_visual="vis", b_roll_query="q")

        with self.assertRaises(ValueError):
            normalize_scene({"id": 1, "narration": "Test", "duration_s": float("nan")})

        with self.assertRaises(ValidationError):
            Scene(id=1, narration="Test", duration_s=float("inf"), b_roll_visual="vis", b_roll_query="q")

        with self.assertRaises(ValueError):
            normalize_scene({"id": 1, "narration": "Test", "duration_s": float("inf")})

        # Conflito entre duration_s e estimated_duration_s rejeitado
        with self.assertRaises(ValidationError):
            Scene(id=1, narration="Test", duration_s=5.0, estimated_duration_s=10.0, b_roll_visual="vis", b_roll_query="q")

        with self.assertRaises(ValueError):
            normalize_scene({"id": 1, "narration": "Test", "duration_s": 4.0, "estimated_duration_s": 8.0})

    def test_17_broll_multilingual_portuguese_and_technical_terms(self):
        """17. Extração de B-roll com suporte a português, termos técnicos e numéricos."""
        pt_visual = "Cena de engenheiros na sala de controle observando os monitores da Apollo 11 em 1969"
        query_pt = extract_concise_broll_query(pt_visual)
        q_words = query_pt.split()
        self.assertGreaterEqual(len(q_words), 2)
        self.assertLessEqual(len(q_words), 5)
        # Deve ter removido as stop words em português "na", "de", "os", "da", "em", "cena"
        self.assertNotIn("cena", query_pt.lower().split())
        self.assertNotIn("na", query_pt.lower().split())
        # Deve ter preservado termos técnicos e numéricos
        self.assertTrue(any(term in query_pt for term in ["Apollo", "11", "1969", "engenheiros", "controle"]))

    def test_18_broll_query_normalization_when_exceeding_five_words(self):
        """18. Normalização determinística de query extensa fornecida com mais de 5 palavras."""
        long_query = "cinematic wide shot of golden bullion bars stacked high in federal reserve bank vault"
        normalized_q = normalize_broll_query(long_query, min_words=2, max_words=5)
        q_words = normalized_q.split()
        self.assertGreaterEqual(len(q_words), 2)
        self.assertLessEqual(len(q_words), 5)
        # Termos conceituais relevantes preservados
        self.assertTrue(any(term in normalized_q.lower() for term in ["golden", "bullion", "bars", "vault", "reserve"]))

        # Quando Scene recebe query com > 5 palavras, normaliza automaticamente
        scene = Scene(
            id=1,
            narration="Gold reserves held securely.",
            duration_s=5.0,
            b_roll_query=long_query,
        )
        self.assertLessEqual(len(scene.b_roll_query.split()), 5)
        self.assertGreaterEqual(len(scene.b_roll_query.split()), 2)

    def test_19_broll_query_single_word_no_fabrication(self):
        """19. Quando entrada só tem 1 palavra significativa, não fabricar termos semânticos falsos."""
        single_word_visual = "Bitcoin"
        concise = extract_concise_broll_query(single_word_visual)
        self.assertEqual(concise, "Bitcoin")
        self.assertEqual(len(concise.split()), 1)

        # Entrada vazia ou apenas caracteres especiais
        empty_res = extract_concise_broll_query("!@#$%^&*")
        self.assertEqual(empty_res, "")

    def test_20_broll_visual_explicit_interpretation_from_query_only(self):
        """20. Quando existir apenas b_roll_query, gerar descrição visual explícita sem inventar fatos."""
        scene = Scene(
            id=1,
            narration="Artificial intelligence transforms enterprise workflows.",
            duration_s=5.0,
            b_roll_query="AI neural network servers",
        )
        self.assertEqual(scene.b_roll_query, "AI neural network servers")
        self.assertTrue(scene.b_roll_visual.startswith("Cinematic visual representation based on:"))
        self.assertIn("AI neural network servers", scene.b_roll_visual)

    def test_21_enums_aliases_and_invalid_values_rejection(self):
        """21. Enums: aliases canônicos (ShotType, VisualType, AssetSource), aliases legados e rejeição de desconhecidos."""
        # Aliases de tipos
        self.assertEqual(ShotType, CameraFraming)
        self.assertEqual(VisualType, VisualProductionType)
        self.assertEqual(AssetSource.NASA.value, "nasa_archive")

        # Alias legado 'shake' mapeia para impact_micro_shake
        scene_alias = Scene(
            id=1,
            narration="Testing movement alias.",
            duration_s=5.0,
            b_roll_visual="vis",
            b_roll_query="query",
            camera_movement="shake",
        )
        self.assertEqual(scene_alias.camera_movement, "impact_micro_shake")

        # Valor desconhecido de camera_movement rejeitado no V2
        with self.assertRaises(ValidationError):
            Scene(
                id=1,
                narration="Test invalid movement.",
                duration_s=5.0,
                b_roll_visual="vis",
                b_roll_query="query",
                camera_movement="super_fast_hyper_jump",
            )

        with self.assertRaises(ValueError):
            normalize_scene_v2({
                "narration": "Test invalid movement in normalizer.",
                "camera_movement": "non_existent_zoom",
            })

    def test_22_asset_metadata_provenance_hash_and_license_coherence(self):
        """22. AssetMetadata: validação rigorosa de hash SHA-256 e coerência jurídica."""
        valid_hash = "a" * 64
        invalid_hash = "not_a_valid_sha256_hash"

        # Hash inválido rejeitado
        with self.assertRaises(ValidationError):
            AssetMetadata(
                provider="pexels",
                source_id_or_url="https://pexels.com/123",
                license_type="pexels_license",
                license_proof_ref="https://pexels.com/license",
                local_asset_path="assets/test.mp4",
                sha256_hash=invalid_hash,
            )

        # editorial_only=True com commercial_use_approved=True deve ser rejeitado
        with self.assertRaises(ValidationError):
            AssetMetadata(
                provider="wikimedia",
                source_id_or_url="https://wikimedia.org/wiki/File:Test.jpg",
                license_type="editorial_press",
                license_proof_ref="https://wikimedia.org",
                local_asset_path="assets/editorial.jpg",
                sha256_hash=valid_hash,
                editorial_only=True,
                commercial_use_approved=True,  # Conflito!
            )

        # attribution_required=True sem attribution_text deve ser rejeitado
        with self.assertRaises(ValidationError):
            AssetMetadata(
                provider="wikimedia",
                source_id_or_url="https://wikimedia.org/wiki/File:CC_BY.jpg",
                license_type="cc_by_4_0",
                license_proof_ref="https://creativecommons.org/licenses/by/4.0/",
                local_asset_path="assets/cc_by.jpg",
                sha256_hash=valid_hash,
                attribution_required=True,
                attribution_text=None,  # Conflito!
            )

    def test_23_round_trip_serialization_canonical_stability(self):
        """23. Teste de round-trip de serialização: entrada legada -> modelo -> dump canônico -> modelo -> dump equivalente."""
        legacy_input = {
            "scene_id": 8,
            "text": "Deep sea cables power global data connectivity.",
            "estimated_duration_s": 7.0,
            "visual_description": "macro shot of fiber optic cables under sea floor",
            "type": "proof",
            "tier": 2,
        }

        # 1º salto: entrada legada -> Scene
        scene1 = Scene(**legacy_input)
        dump1 = scene1.model_dump()

        # 2º salto: dump canônico -> Scene novo
        scene2 = Scene(**dump1)
        dump2 = scene2.model_dump()

        # A saída canônica do 2º salto deve ser idêntica à do 1º salto
        self.assertEqual(dump1["id"], dump2["id"])
        self.assertEqual(dump1["narration"], dump2["narration"])
        self.assertEqual(dump1["duration_s"], dump2["duration_s"])
        self.assertEqual(dump1["b_roll_visual"], dump2["b_roll_visual"])
        self.assertEqual(dump1["b_roll_query"], dump2["b_roll_query"])
        self.assertEqual(dump1["scene_type"], dump2["scene_type"])
        self.assertEqual(dump1["estimated_cost_tier"], dump2["estimated_cost_tier"])

    def test_24_deep_immutability_including_errors_and_nested_structures(self):
        """24. Imutabilidade profunda comprovada por cópia e snapshot até mesmo em cenários de exceção."""
        nested_input = {
            "id": 1,
            "narration": "Testing immutability under stress.",
            "duration_s": 5.0,
            "b_roll_visual": "satellite telemetry station dish antenna",
            "visual_cues": [{"sub_list": [1, 2, 3], "nested_dict": {"alpha": "beta"}}],
            "asset_metadata": {
                "provider": "nasa_archive",
                "source_id_or_url": "https://images.nasa.gov",
                "license_type": "public_domain_gov",
                "license_proof_ref": "https://nasa.gov",
                "local_asset_path": "assets/dish.jpg",
                "sha256_hash": "b" * 64,
                "commercial_use_approved": True,
            }
        }

        snapshot_original = copy.deepcopy(nested_input)

        # 1. Normalização normal
        norm = normalize_scene_v2(nested_input)
        # Modifica saída
        norm["visual_cues"][0]["sub_list"].append(999)
        norm["visual_cues"][0]["nested_dict"]["alpha"] = "MUTATED"
        norm["asset_metadata"]["provider"] = "MUTATED_PROVIDER"

        self.assertEqual(nested_input, snapshot_original)
        self.assertEqual(nested_input["visual_cues"][0]["sub_list"], [1, 2, 3])
        self.assertEqual(nested_input["visual_cues"][0]["nested_dict"]["alpha"], "beta")
        self.assertEqual(nested_input["asset_metadata"]["provider"], "nasa_archive")

        # 2. Cenário de erro: entrada com erro proposital
        faulty_input = copy.deepcopy(nested_input)
        faulty_input["duration_s"] = -999.0  # Inválido!
        snapshot_faulty = copy.deepcopy(faulty_input)

        with self.assertRaises(ValueError):
            normalize_scene_v2(faulty_input)

        # A entrada com erro não pode ter sido modificada parcialmente pela tentativa de normalização
        self.assertEqual(faulty_input, snapshot_faulty)

    def test_25_timestamp_sequence_continuity_and_inversion_validation(self):
        """25. Timestamps: validação cronológica contínua e rejeição de inversão temporal."""
        # Sequência contínua com durações variadas
        scenes = [
            {"narration": "Intro scene.", "duration_s": 3.5},
            {"narration": "Middle scene.", "duration_s": 6.5},
            {"narration": "Outro scene.", "duration_s": 10.0},
        ]
        norm = normalize_scenes_v2(scenes)
        self.assertEqual(norm[0]["timestamp"], "0:00-0:03")
        self.assertEqual(norm[1]["timestamp"], "0:03-0:10")
        self.assertEqual(norm[2]["timestamp"], "0:10-0:20")

        # Timestamps fornecidos manualmente de forma mista: continuidade garantida
        mixed_scenes = [
            {"narration": "First scene.", "timestamp": "0:00-0:10"},
            {"narration": "Second scene with auto timestamp.", "duration_s": 5.0},
            {"narration": "Third scene manual.", "timestamp": "0:15-0:30"},
        ]
        norm_mixed = normalize_scenes_v2(mixed_scenes)
        self.assertEqual(norm_mixed[0]["timestamp"], "0:00-0:10")
        self.assertEqual(norm_mixed[1]["timestamp"], "0:10-0:15")
        self.assertEqual(norm_mixed[2]["timestamp"], "0:15-0:30")

        # Inconsistência cronológica entre cenas: cena 2 retrocede no tempo
        inconsistent_scenes = [
            {"narration": "Scene one ends at 10s.", "timestamp": "0:00-0:10"},
            {"narration": "Scene two starts at 5s (overlap / retrocession).", "timestamp": "0:05-0:15"},
        ]
        with self.assertRaises(ValueError):
            normalize_scenes_v2(inconsistent_scenes)

        # Inversão temporal interna proposital deve ser rejeitada
        with self.assertRaises(ValueError):
            normalize_scene({
                "narration": "Inverted time test.",
                "timestamp": "0:15-0:05",  # Invertido: 15s > 5s
            })


if __name__ == "__main__":
    unittest.main()

