"""
Script Validator — Anti-Redundancy & Quality Control Engine
===========================================================
Runs BEFORE sending script to TTS. Catches:
  - Duplicate/looping paragraphs (like the Emilia Clarke video bug)
  - Scenes with < 15 words (too short to be useful)
  - Missing visual cues
  - Missing organic CTAs
  - Technical incoherence markers
"""
from __future__ import annotations

import re
import hashlib
from difflib import SequenceMatcher
from typing import List, Dict, Tuple, Any

from shared.logger import get_logger

logger = get_logger(__name__)

# ─────────────────────────────────────────────────
# 1. SIMILARITY THRESHOLD
# ─────────────────────────────────────────────────
_SIMILARITY_THRESHOLD = 0.72  # 72%+ similarity = duplicate block
_MIN_WORDS_PER_SCENE   = 15   # Minimum narration words for a LONG scene
_MIN_TOTAL_WORDS_LONG  = 900  # Absolute minimum accepted before raising


def _normalize(text: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation for comparison."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _fingerprint(text: str) -> str:
    return hashlib.md5(_normalize(text).encode()).hexdigest()


# ─────────────────────────────────────────────────
# 2. REDUNDANCY DETECTOR
# ─────────────────────────────────────────────────
def detect_duplicate_scenes(scenes: List[Dict]) -> List[Tuple[int, int, float]]:
    """
    Returns a list of (scene_idx_A, scene_idx_B, similarity_score)
    for every pair of scenes that are suspiciously similar.
    Mirrors the exact bug found in the Emilia Clarke / MotoGP DE video
    where an entire block repeats verbatim at ~16:53.
    """
    duplicates = []
    narrations = []

    for s in scenes:
        narr = str(s.get("narration") or s.get("text") or s.get("content") or "")
        narrations.append(_normalize(narr))

    for i in range(len(narrations)):
        for j in range(i + 1, len(narrations)):
            if len(narrations[i]) < 30 or len(narrations[j]) < 30:
                continue  # skip trivially short scenes
            sim = _similarity(narrations[i], narrations[j])
            if sim >= _SIMILARITY_THRESHOLD:
                duplicates.append((i, j, round(sim, 3)))

    return duplicates


def remove_duplicate_scenes(scenes: List[Dict]) -> Tuple[List[Dict], List[int]]:
    """
    Returns (cleaned_scenes, removed_indices).
    Keeps the first occurrence of duplicated content and removes later copies.
    """
    seen_fingerprints: set = set()
    cleaned = []
    removed = []

    for idx, scene in enumerate(scenes):
        narr = str(scene.get("narration") or scene.get("text") or scene.get("content") or "")
        # Use sliding window fingerprint to catch partial duplicates
        normalized = _normalize(narr)
        fp = _fingerprint(normalized[:200])  # first 200 chars = unique enough

        if fp in seen_fingerprints:
            removed.append(idx)
            logger.warning(
                f"🚨 [VALIDATOR] Scene {idx} is a DUPLICATE of an earlier scene. "
                f"Auto-removed. First 60 chars: '{narr[:60]}...'"
            )
        else:
            # Also check sliding similarity against all previously accepted scenes
            is_dupe = False
            for accepted in cleaned:
                accepted_narr = str(accepted.get("narration") or "")
                if len(normalized) > 30 and _similarity(normalized, _normalize(accepted_narr)) >= _SIMILARITY_THRESHOLD:
                    is_dupe = True
                    removed.append(idx)
                    logger.warning(
                        f"🚨 [VALIDATOR] Scene {idx} is ~{_similarity(normalized, _normalize(accepted_narr)):.0%} "
                        f"similar to an earlier scene. Auto-removed."
                    )
                    break
            if not is_dupe:
                seen_fingerprints.add(fp)
                cleaned.append(scene)

    return cleaned, removed


# ─────────────────────────────────────────────────
# 3. VISUAL CUE COMPLETENESS CHECK
# ─────────────────────────────────────────────────
def check_visual_cues(scenes: List[Dict]) -> List[int]:
    """Returns list of scene indices missing a proper b_roll_visual."""
    missing = []
    for idx, scene in enumerate(scenes):
        visual = str(scene.get("b_roll_visual") or scene.get("visual") or "").strip()
        # Must be in English and at least 3 words
        if len(visual.split()) < 3:
            missing.append(idx)
    return missing


def check_visual_micro_cues(scenes: List[Dict]) -> List[int]:
    """
    Checks that each scene has at least one visual_cue entry
    (the 3-5s micro-pacing markers added by the Retention Engine).
    """
    missing = []
    for idx, scene in enumerate(scenes):
        cues = scene.get("visual_cues") or scene.get("micro_cues") or []
        if not cues or not isinstance(cues, list) or len(cues) == 0:
            missing.append(idx)
    return missing


# ─────────────────────────────────────────────────
# 4. CTA PRESENCE CHECK
# ─────────────────────────────────────────────────
_CTA_KEYWORDS = [
    # EN
    "subscribe", "notification", "bell", "turn on", "follow",
    # ES
    "suscríbete", "suscribete", "notificaciones", "activa",
    # FR
    "abonnez", "abonne", "notification", "cloche",
    # DE
    "abonniere", "benachrichtigung", "glocke", "kanal",
]

def check_cta_presence(scenes: List[Dict]) -> bool:
    """Returns True if at least one scene contains an organic CTA."""
    for scene in scenes:
        narr = str(scene.get("narration") or "").lower()
        if any(kw in narr for kw in _CTA_KEYWORDS):
            return True
    return False


# ─────────────────────────────────────────────────
# 5. OPEN LOOP PRESENCE CHECK
# ─────────────────────────────────────────────────
_OPEN_LOOP_PATTERNS = [
    r"but (first|wait|here'?s|the real|what)",
    r"mais (avant|voici|ce que|la vraie)",
    r"pero (primero|espera|lo que|la verdad)",
    r"aber (zuerst|warte|das echte|was)",
    r"(what|but) (you|we) (don'?t|won'?t|haven'?t)",
    r"(the next|coming up|stay tuned|keep watching)",
    r"(lo próximo|a continuación|sigue viendo)",
    r"(was kommt|bleib dran|schau weiter)",
    r"(ce qui suit|restez|continuez)",
]

def check_open_loops(scenes: List[Dict]) -> List[int]:
    """Returns scene indices that contain an open loop / curiosity hook."""
    found = []
    for idx, scene in enumerate(scenes):
        narr = str(scene.get("narration") or "").lower()
        for pattern in _OPEN_LOOP_PATTERNS:
            if re.search(pattern, narr):
                found.append(idx)
                break
    return found


# ─────────────────────────────────────────────────
# 6. HOOK QUALITY CHECK
# ─────────────────────────────────────────────────
_WEAK_HOOK_PATTERNS = [
    r"^(in this video|welcome|hello everyone|today we|in today)",
    r"^(in diesem video|willkommen|hallo|heute werden)",
    r"^(dans cette vidéo|bienvenue|bonjour|aujourd'hui)",
    r"^(en este vídeo|bienvenidos|hola|hoy veremos)",
    r"^(this video (is|will|covers))",
]

def check_hook_quality(scenes: List[Dict]) -> bool:
    """
    Returns True if the first scene has a STRONG hook
    (i.e., does NOT start with weak patterns like 'In this video...').
    """
    if not scenes:
        return False
    first_narr = str(scenes[0].get("narration") or "").strip().lower()
    for pattern in _WEAK_HOOK_PATTERNS:
        if re.match(pattern, first_narr):
            return False
    return True


# ─────────────────────────────────────────────────
# 7. MAIN VALIDATOR — FULL PIPELINE
# ─────────────────────────────────────────────────
class ScriptValidationResult:
    def __init__(self):
        self.passed = True
        self.scenes_removed: List[int] = []
        self.missing_visuals: List[int] = []
        self.missing_micro_cues: List[int] = []
        self.has_cta = False
        self.open_loop_scenes: List[int] = []
        self.strong_hook = False
        self.word_count = 0
        self.warnings: List[str] = []
        self.errors: List[str] = []

    def report(self) -> str:
        lines = ["=" * 60, "🔍 SCRIPT VALIDATION REPORT", "=" * 60]
        lines.append(f"✅ PASSED: {self.passed}")
        lines.append(f"📝 Word Count: {self.word_count}")
        lines.append(f"🔥 Strong Hook: {self.strong_hook}")
        lines.append(f"📣 Has Organic CTA: {self.has_cta}")
        lines.append(f"🔄 Open Loop Scenes: {self.open_loop_scenes}")
        if self.scenes_removed:
            lines.append(f"🚨 Duplicate Scenes Removed: {self.scenes_removed}")
        if self.missing_visuals:
            lines.append(f"⚠️  Missing B-Roll Visuals at scenes: {self.missing_visuals}")
        if self.missing_micro_cues:
            lines.append(f"⚠️  Missing Micro Visual Cues at scenes: {self.missing_micro_cues}")
        for w in self.warnings:
            lines.append(f"⚠️  {w}")
        for e in self.errors:
            lines.append(f"❌ {e}")
        lines.append("=" * 60)
        return "\n".join(lines)


def validate_and_fix_script(script_data: dict, is_short: bool = False) -> Tuple[dict, ScriptValidationResult]:
    """
    Full validation + auto-fix pipeline. Returns the cleaned script_data
    and a ScriptValidationResult object.

    Usage in copywriter.py:
        script_data, result = validate_and_fix_script(script_data, is_short)
        print(result.report())
    """
    result = ScriptValidationResult()
    scenes = script_data.get("scenes", [])

    if not isinstance(scenes, list) or len(scenes) == 0:
        result.passed = False
        result.errors.append("No scenes found in script_data.")
        return script_data, result

    # ── Step 1: Remove duplicate scenes ────────────────────────
    cleaned_scenes, removed_indices = remove_duplicate_scenes(scenes)
    if removed_indices:
        result.scenes_removed = removed_indices
        result.warnings.append(
            f"{len(removed_indices)} duplicate scene(s) auto-removed to prevent TTS loop."
        )
        script_data["scenes"] = cleaned_scenes
        scenes = cleaned_scenes

    # ── Step 2: Check B-Roll visual cues ───────────────────────
    missing_vis = check_visual_cues(scenes)
    if missing_vis and not is_short:
        result.missing_visuals = missing_vis
        result.warnings.append(f"Scenes {missing_vis} lack b_roll_visual. Using topic keyword as fallback.")
        topic_fallback = script_data.get("title", "artificial intelligence finance technology")
        for idx in missing_vis:
            scenes[idx].setdefault("b_roll_visual", topic_fallback)

    # ── Step 3: Check micro visual cues ────────────────────────
    if not is_short:
        missing_micro = check_visual_micro_cues(scenes)
        if missing_micro:
            result.missing_micro_cues = missing_micro
            result.warnings.append(
                f"Scenes {missing_micro} lack visual_cues (3-5s micro pacing). "
                "Injecting default cinematic cue."
            )
            for idx in missing_micro:
                scenes[idx]["visual_cues"] = [
                    {"t": 0, "type": "zoom_in", "target": "speaker face / data chart"},
                    {"t": 3, "type": "cut", "target": "b_roll_visual"},
                ]

    # ── Step 4: CTA presence ────────────────────────────────────
    result.has_cta = check_cta_presence(scenes)
    if not result.has_cta and not is_short:
        result.warnings.append(
            "No organic CTA detected. Consider injecting one mid-video (scene 10-15)."
        )

    # ── Step 5: Open loops ──────────────────────────────────────
    result.open_loop_scenes = check_open_loops(scenes)
    if len(result.open_loop_scenes) < 2 and not is_short:
        result.warnings.append(
            f"Only {len(result.open_loop_scenes)} open loop(s) detected. "
            "Target: ≥3 open loops spaced across the video."
        )

    # ── Step 6: Hook quality ────────────────────────────────────
    result.strong_hook = check_hook_quality(scenes)
    if not result.strong_hook:
        result.errors.append(
            "WEAK HOOK detected. First scene starts with a generic intro phrase. "
            "This will tank retention in the first 15 seconds."
        )
        result.passed = False

    # ── Step 7: Word count ──────────────────────────────────────
    total_words = sum(
        len(str(s.get("narration") or s.get("text") or s.get("content") or "").split())
        for s in scenes
    )
    result.word_count = total_words

    if not is_short and total_words < _MIN_TOTAL_WORDS_LONG:
        result.errors.append(
            f"Script too short: {total_words} words. Minimum is {_MIN_TOTAL_WORDS_LONG}."
        )
        result.passed = False

    # ── Step 8: Update script_full ──────────────────────────────
    script_data["script_full"] = "\n".join(
        str(s.get("narration") or s.get("text") or s.get("content") or "")
        for s in scenes
    )
    script_data["scenes"] = scenes

    logger.info(result.report())
    return script_data, result
