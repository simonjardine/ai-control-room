"""Portable provider configuration for AI Control Room."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
EXAMPLE_PATH = APP_DIR / "config.example.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "providers": {
        "openai": {"api_key": "", "base_url": "https://api.openai.com/v1"},
        "openrouter": {"api_key": "", "base_url": "https://openrouter.ai/api/v1"},
        "xai": {"api_key": "", "base_url": "https://api.x.ai/v1"},
        "kimi": {"api_key": "", "base_url": "https://api.moonshot.ai/v1"},
        "local": {
            "api_key": "", "base_url": "http://127.0.0.1:18434/v1", "models_path": ""
        },
    },
    "room": {
        "participants": {
            "atlas": {"provider": "openai", "model": ""},
            "jade": {"provider": "openrouter", "model": ""},
            "meridian": {"provider": "openrouter", "model": ""},
            "frontier": {"provider": "xai", "model": ""},
            "haven": {"provider": "local", "model": ""},
            "horizon": {"provider": "kimi", "model": ""},
        }
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config(path: Path | None = None) -> dict[str, Any]:
    path = Path(path) if path is not None else CONFIG_PATH
    source = path if path.exists() else path.with_name(EXAMPLE_PATH.name)
    try:
        data = json.loads(source.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("Expected a configuration object")
    except (OSError, ValueError):
        return copy.deepcopy(DEFAULT_CONFIG)
    # Older local installs used role or race names. Import their assignments only;
    # the ship/race implementation is not needed to open their room configuration.
    if not data.get("room", {}).get("participants") and (data.get("roles") or data.get("race")):
        from room import room_specs
        data.setdefault("room", {})["participants"] = room_specs(data)
    return _deep_merge(DEFAULT_CONFIG, data)


def save_config(cfg: dict[str, Any], path: Path | None = None) -> None:
    from room_store import write_json
    write_json(Path(path) if path is not None else CONFIG_PATH, cfg)
