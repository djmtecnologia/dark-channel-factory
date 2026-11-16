"""
Blender 3D Headless Worker — Integration Stub (Phase 1)
=======================================================
Prepares the integration architecture for Blender 3D rendering.
Does NOT require Blender installation.
Safely detects environment availability and provides automatic fallback.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any, Dict, Optional

from shared.logger import get_logger

logger = get_logger(__name__)


class Blender3DWorker:
    """
    Independent worker for 3D shots rendering via Blender Headless.
    Features automatic fallback when Blender is not installed.
    """

    def __init__(self, executable_path: Optional[str] = None):
        self.enabled = os.getenv("ENABLE_BLENDER_RENDER", "false").lower() in ("true", "1", "yes")
        self.timeout_s = int(os.getenv("BLENDER_RENDER_TIMEOUT_S", "60"))
        self.executable = self._find_blender(executable_path)

    def _find_blender(self, explicit_path: Optional[str]) -> Optional[str]:
        if explicit_path and os.path.exists(explicit_path):
            return explicit_path

        env_path = os.getenv("BLENDER_EXECUTABLE_PATH")
        if env_path and os.path.exists(env_path):
            return env_path

        # Standard PATH lookup
        found = shutil.which("blender")
        if found:
            return found

        # Common Windows installation locations
        common_windows_paths = [
            r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender 4.1\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender 4.0\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender 3.6\blender.exe",
        ]
        for p in common_windows_paths:
            if os.path.exists(p):
                return p

        return None

    def is_available(self) -> bool:
        """Returns True only if feature flag is enabled and Blender binary is located."""
        return self.enabled and (self.executable is not None)

    def render_3d_shot(
        self,
        scene_contract: Dict[str, Any],
        output_path: str,
        fallback_handler: Optional[Any] = None,
    ) -> Optional[str]:
        """
        Attempts to render the 3D scene using Blender headless.
        If unavailable or fails, gracefully delegates to fallback_handler.
        """
        if not self.is_available():
            logger.info("ℹ️ [Blender Worker] Blender is not enabled or binary not found. Triggering fallback.")
            if fallback_handler and callable(fallback_handler):
                return fallback_handler(scene_contract, output_path)
            return None

        # When Blender is installed and enabled (Future Phase 4):
        logger.info(f"🚀 [Blender Worker] Executing headless render for scene with {self.executable}...")
        try:
            # Subprocess execution template:
            # cmd = [self.executable, "-b", "-P", script_path, "--", ...]
            # For Phase 1 stub, safe execution check:
            res = subprocess.run([self.executable, "-v"], capture_output=True, text=True, timeout=10)
            logger.info(f"✅ [Blender Worker] Detected Blender: {res.stdout.splitlines()[0] if res.stdout else 'OK'}")
            return output_path
        except Exception as e:
            logger.warning(f"⚠️ [Blender Worker] Render failed or timed out: {e}. Falling back.")
            if fallback_handler and callable(fallback_handler):
                return fallback_handler(scene_contract, output_path)
            return None
