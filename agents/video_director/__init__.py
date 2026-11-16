"""
AI Video Director Package — Dark Channel Factory (Fase 2)
Fornece inteligência de interpretação dramática (NarrativeAnalyzer)
e direção audiovisual cinematográfica (VisualDirector).
"""
from agents.video_director.models import (
    EvidenceType,
    NarrativeAnalysisResult,
    PlannedScene,
    SceneNarrativeInterpretation,
    ScenePlanResult,
    SceneVisualDirection,
    VisualBible,
    VisualDirectionResult,
    VisualImpactPotential,
    VisualLanguage,
)
from agents.video_director.narrative_analyzer import (
    NarrativeAnalysisError,
    NarrativeAnalyzer,
)
from agents.video_director.scene_planner import (
    DurationMismatchError,
    IncompleteLLMResponseError,
    ScenePlanner,
    ScenePlannerError,
    ScenePlannerValidationError,
    VisualContinuityError,
)
from agents.video_director.visual_director import (
    VisualDirectionError,
    VisualDirector,
)

from agents.video_director.director_engine import (
    AIVideoDirector,
    AIVideoDirectorError,
)
from agents.video_director.master_plan_models import (
    ExecutionMetadata,
    MasterAVPlan,
    StageExecutionMetrics,
)
from agents.video_director.security import sanitize_secret

__all__ = [
    "EvidenceType",
    "NarrativeAnalysisResult",
    "PlannedScene",
    "SceneNarrativeInterpretation",
    "ScenePlanResult",
    "SceneVisualDirection",
    "VisualBible",
    "VisualDirectionResult",
    "VisualImpactPotential",
    "VisualLanguage",
    "NarrativeAnalysisError",
    "NarrativeAnalyzer",
    "DurationMismatchError",
    "IncompleteLLMResponseError",
    "ScenePlanner",
    "ScenePlannerError",
    "ScenePlannerValidationError",
    "VisualContinuityError",
    "VisualDirectionError",
    "VisualDirector",
    "AIVideoDirector",
    "AIVideoDirectorError",
    "MasterAVPlan",
    "ExecutionMetadata",
    "StageExecutionMetrics",
    "sanitize_secret",
]
