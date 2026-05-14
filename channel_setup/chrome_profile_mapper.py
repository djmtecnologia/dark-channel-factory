"""
Chrome Profile Mapper — Dark Channel Factory
Lê os perfis do Chrome existentes e mapeia nome → email → pasta.
Usa os perfis já logados, sem precisar de senha.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

CHROME_USER_DATA = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"


def get_chrome_profiles() -> list[dict]:
    """
    Lê todos os perfis do Chrome e retorna info de cada um.
    """
    profiles: list[dict] = []

    if not CHROME_USER_DATA.exists():
        print(f"Chrome User Data not found at: {CHROME_USER_DATA}")
        return profiles

    # Pasta do Local State (tem o mapa de perfis)
    local_state_path = CHROME_USER_DATA / "Local State"
    if local_state_path.exists():
        with open(local_state_path, encoding="utf-8") as f:
            local_state = json.load(f)

        profile_info = local_state.get("profile", {}).get("info_cache", {})

        for folder, info in profile_info.items():
            profile_dir = CHROME_USER_DATA / folder
            if not profile_dir.exists():
                continue

            profiles.append({
                "folder": folder,
                "path": str(profile_dir),
                "name": info.get("name", folder),
                "email": info.get("user_name", "unknown"),
                "gaia_name": info.get("gaia_name", ""),
                "is_consented_primary_account": info.get("is_consented_primary_account", False),
            })

    return sorted(profiles, key=lambda p: p["folder"])


def print_profiles() -> None:
    """Exibe todos os perfis encontrados."""
    profiles = get_chrome_profiles()
    print("\n📋 Chrome Profiles Found:\n")
    print(f"{'#':<4} {'Folder':<12} {'Name':<30} {'Email'}")
    print("-" * 80)
    for i, p in enumerate(profiles, 1):
        print(f"{i:<4} {p['folder']:<12} {p['name']:<30} {p['email']}")
    print(f"\nTotal: {len(profiles)} profiles")
    print(f"Chrome User Data: {CHROME_USER_DATA}\n")
    return profiles


if __name__ == "__main__":
    print_profiles()
