"""
Production Profiles — Dark Channel Factory
==========================================
Centralized, testable, and versioned configuration of video production profiles.

Supported profiles:
- PILOT_5MIN: 270-330s, 600-850 words, 12-18 scenes, 1280x720.
- STANDARD_10MIN: 480-720s, 1200-1600 words, 30-45 scenes, 1280x720.
- DOCUMENTARY_20MIN: 900-1800s, 2200-4000 words, 60-90 scenes, 1280x720.
- SHORTS: 20-59s, 100-200 words, 3-8 scenes, 720x1280 (vertical).

When no explicit profile is configured, legacy behavior is preserved.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Tuple, Union


class ProductionProfileType(str, Enum):
    PILOT_5MIN = "PILOT_5MIN"
    STANDARD_10MIN = "STANDARD_10MIN"
    DOCUMENTARY_20MIN = "DOCUMENTARY_20MIN"
    SHORTS = "SHORTS"


@dataclass(frozen=True)
class ProductionProfileConfig:
    """Immutable specification for a video production profile."""
    name: str
    min_duration_s: float
    max_duration_s: float
    min_words: int
    max_words: int
    min_scenes: int
    max_scenes: int
    target_width: int
    target_height: int
    is_vertical: bool = False
    description: str = ""


# Centralized registry of production profiles
PROFILES: Dict[str, ProductionProfileConfig] = {
    ProductionProfileType.PILOT_5MIN.value: ProductionProfileConfig(
        name=ProductionProfileType.PILOT_5MIN.value,
        min_duration_s=270.0,
        max_duration_s=330.0,
        min_words=600,
        max_words=850,
        min_scenes=12,
        max_scenes=18,
        target_width=1280,
        target_height=720,
        is_vertical=False,
        description="Documentário piloto de 5 minutos para validação rápida e benchmark",
    ),
    ProductionProfileType.STANDARD_10MIN.value: ProductionProfileConfig(
        name=ProductionProfileType.STANDARD_10MIN.value,
        min_duration_s=480.0,
        max_duration_s=720.0,
        min_words=1200,
        max_words=1600,
        min_scenes=30,
        max_scenes=45,
        target_width=1280,
        target_height=720,
        is_vertical=False,
        description="Produção padrão de 8 a 12 minutos",
    ),
    ProductionProfileType.DOCUMENTARY_20MIN.value: ProductionProfileConfig(
        name=ProductionProfileType.DOCUMENTARY_20MIN.value,
        min_duration_s=900.0,
        max_duration_s=1800.0,
        min_words=2200,
        max_words=4000,
        min_scenes=60,
        max_scenes=90,
        target_width=1280,
        target_height=720,
        is_vertical=False,
        description="Documentário aprofundado de 15 a 30 minutos",
    ),
    ProductionProfileType.SHORTS.value: ProductionProfileConfig(
        name=ProductionProfileType.SHORTS.value,
        min_duration_s=20.0,
        max_duration_s=59.0,
        min_words=100,
        max_words=200,
        min_scenes=3,
        max_scenes=8,
        target_width=720,
        target_height=1280,
        is_vertical=True,
        description="Vídeo vertical dinâmico de 20 a 59 segundos",
    ),
}

# Aliases for flexible matching
_PROFILE_ALIASES: Dict[str, str] = {
    "PILOT": ProductionProfileType.PILOT_5MIN.value,
    "PILOT_5MIN": ProductionProfileType.PILOT_5MIN.value,
    "PILOT_5M": ProductionProfileType.PILOT_5MIN.value,
    "PILOTO": ProductionProfileType.PILOT_5MIN.value,
    "STANDARD": ProductionProfileType.STANDARD_10MIN.value,
    "STANDARD_10MIN": ProductionProfileType.STANDARD_10MIN.value,
    "STANDARD_10M": ProductionProfileType.STANDARD_10MIN.value,
    "DOCUMENTARY": ProductionProfileType.DOCUMENTARY_20MIN.value,
    "DOCUMENTARY_20MIN": ProductionProfileType.DOCUMENTARY_20MIN.value,
    "DOCUMENTARY_20M": ProductionProfileType.DOCUMENTARY_20MIN.value,
    "DOC": ProductionProfileType.DOCUMENTARY_20MIN.value,
    "SHORT": ProductionProfileType.SHORTS.value,
    "SHORTS": ProductionProfileType.SHORTS.value,
}


def resolve_profile(
    job: Any = None,
    profile_name: Optional[Union[str, ProductionProfileType]] = None,
    video_type: Optional[str] = None,
    force_explicit_only: bool = False,
) -> Optional[ProductionProfileConfig]:
    """
    Resolves the production profile from a job object, dictionary, or explicit profile name.
    
    Rules:
    1. If an explicit profile_name is given, resolve and return it.
    2. If job has 'production_profile' set (and non-empty), resolve and return it.
    3. If force_explicit_only is True, return None when no explicit profile is configured.
    4. When no explicit profile is found, return None to preserve legacy validator behavior.
    """
    raw_name = None

    if profile_name is not None:
        raw_name = profile_name.value if isinstance(profile_name, ProductionProfileType) else str(profile_name)
    elif job is not None:
        if hasattr(job, "production_profile") and getattr(job, "production_profile"):
            raw_name = str(getattr(job, "production_profile"))
        elif isinstance(job, dict):
            raw_name = job.get("production_profile") or job.get("profile")

    if raw_name:
        normalized_key = str(raw_name).strip().upper()
        resolved_key = _PROFILE_ALIASES.get(normalized_key, normalized_key)
        if resolved_key in PROFILES:
            return PROFILES[resolved_key]

    # No explicit profile configured -> legacy behavior preserved
    return None


def validate_duration(
    duration_s: float,
    profile: Optional[Union[str, ProductionProfileConfig]] = None,
    video_type: str = "LONG",
) -> Tuple[bool, Optional[str]]:
    """
    Validates duration against the specified profile or legacy rules.
    
    Returns:
        (passed: bool, error_message: Optional[str])
    """
    config: Optional[ProductionProfileConfig] = None
    if isinstance(profile, ProductionProfileConfig):
        config = profile
    elif profile is not None:
        config = resolve_profile(profile_name=profile)

    if config is not None:
        if duration_s < config.min_duration_s:
            return False, (
                f"Duração ({duration_s:.1f}s) abaixo do mínimo para {config.name} "
                f"(Mínimo: {config.min_duration_s:.1f}s)."
            )
        if duration_s > config.max_duration_s:
            return False, (
                f"Duração ({duration_s:.1f}s) acima do máximo para {config.name} "
                f"(Máximo: {config.max_duration_s:.1f}s)."
            )
        return True, None

    # Legacy fallback:
    # SHORT: min 20s
    # LONG: min 480s, no max upper bound
    expected_min = 20.0 if str(video_type).upper() == "SHORT" else 480.0
    if duration_s < expected_min:
        return False, (
            f"Duração ({duration_s:.1f}s) abaixo do mínimo para {video_type} "
            f"(Mínimo: {expected_min:.1f}s)."
        )
    return True, None


def validate_script_word_count(
    word_count: int,
    profile: Optional[Union[str, ProductionProfileConfig]] = None,
    is_short: bool = False,
    validator_type: str = "qc",
) -> Tuple[bool, Optional[str]]:
    """
    Validates script word count.
    
    validator_type can be:
      - 'qc' / 'script': checks minimum words required for script validation
      - 'content': checks against content validator thresholds
      
    Returns:
        (passed: bool, error_message: Optional[str])
    """
    config: Optional[ProductionProfileConfig] = None
    if isinstance(profile, ProductionProfileConfig):
        config = profile
    elif profile is not None:
        config = resolve_profile(profile_name=profile)

    if config is not None:
        if word_count < config.min_words:
            return False, (
                f"Script too short: {word_count} words (minimum {config.min_words} for {config.name})"
            )
        return True, None

    # Legacy rules:
    if is_short:
        if word_count < 100:
            return False, f"Short script too short: {word_count} words (minimum 100)"
        return True, None

    # For long videos without explicit profile:
    if validator_type == "content":
        legacy_min = 1800  # Legacy MIN_WORD_COUNT in content_validator
    else:
        legacy_min = 900   # Legacy _MIN_TOTAL_WORDS_LONG in script_validator

    if word_count < legacy_min:
        return False, f"Script too short: {word_count} words (minimum {legacy_min})"
    return True, None


def validate_scene_count(
    scene_count: int,
    profile: Optional[Union[str, ProductionProfileConfig]] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Validates scene count against profile range.
    """
    config: Optional[ProductionProfileConfig] = None
    if isinstance(profile, ProductionProfileConfig):
        config = profile
    elif profile is not None:
        config = resolve_profile(profile_name=profile)

    if config is not None:
        if scene_count < config.min_scenes:
            return False, (
                f"Scene count ({scene_count}) below recommended range for {config.name} "
                f"({config.min_scenes}-{config.max_scenes} scenes)"
            )
        if scene_count > config.max_scenes:
            return False, (
                f"Scene count ({scene_count}) above recommended range for {config.name} "
                f"({config.min_scenes}-{config.max_scenes} scenes)"
            )
        return True, None

    return True, None
