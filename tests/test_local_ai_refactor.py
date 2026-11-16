from agents.copywriter.agent import localize_script
from agents.video_editor.tts_engine import get_voice_for_language


class FakeOllama:
    def chat_json(self, *_args, **_kwargs):
        return {
            "title": "Lokaler Titel",
            "scenes": [
                {
                    "id": 99,
                    "scene_type": "hook",
                    "duration_s": 1,
                    "emotion": "shock",
                    "b_roll_query": "wrong",
                    "text": "Lokalisierter Text",
                }
            ],
        }


def test_localization_restores_matrix_visual_contract():
    matrix = {
        "title": "Matrix",
        "scenes": [{
            "id": 1,
            "scene_type": "solution",
            "duration_s": 25,
            "emotion": "curiosity",
            "b_roll_query": "person using laptop",
            "text": "Base text",
        }],
    }
    localized = localize_script(
        matrix,
        {"country": "Germany", "language": "de-DE", "currency": "EUR"},
        FakeOllama(),
    )
    scene = localized["scenes"][0]
    assert scene["text"] == "Lokalisierter Text"
    assert {key: scene[key] for key in ("id", "scene_type", "duration_s", "emotion", "b_roll_query")} == {
        "id": 1,
        "scene_type": "solution",
        "duration_s": 25,
        "emotion": "curiosity",
        "b_roll_query": "person using laptop",
    }


def test_required_voice_routing():
    assert get_voice_for_language("en-US") == "en-US-ChristopherNeural"
    assert get_voice_for_language("de-DE") == "de-DE-KillianNeural"
    assert get_voice_for_language("es-ES") == "es-ES-AlvaroNeural"
    assert get_voice_for_language("fr-FR") == "fr-FR-HenriNeural"
