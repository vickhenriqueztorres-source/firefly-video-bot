"""Perfil de geracao compartilhado com o pipeline TypeScript."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final


PROFILE_PATH: Final[Path] = (
    Path(__file__).resolve().parents[2] / "config" / "fireflyGenerationProfile.json"
)


def _load_profile() -> dict[str, object]:
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    expected = {
        "model": "Kling 2.5 Turbo",
        "resolution": "1080p",
        "aspect_ratio": "16:9",
        "fps": 24,
        "duration_seconds": 5,
        "requires_first_frame": True,
    }
    mismatches = {
        key: (profile.get(key), value)
        for key, value in expected.items()
        if profile.get(key) != value
    }
    if mismatches:
        raise RuntimeError(f"FIREFLY_GENERATION_PROFILE_INVALID:{mismatches}")
    return profile


GENERATION_PROFILE: Final[dict[str, object]] = _load_profile()
BATCH_DEFAULTS: Final[dict[str, object]] = {
    "model": GENERATION_PROFILE["model"],
    "resolution": GENERATION_PROFILE["resolution"],
    "aspect_ratio": GENERATION_PROFILE["aspect_ratio"],
    "fps": GENERATION_PROFILE["fps"],
    "duration_seconds": GENERATION_PROFILE["duration_seconds"],
    "requires_first_frame": GENERATION_PROFILE["requires_first_frame"],
    "generate_audio": GENERATION_PROFILE["generate_audio"],
}
