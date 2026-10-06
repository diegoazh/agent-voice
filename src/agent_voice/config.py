"""Persistent settings (JSON). Never holds reply text."""

import json
import math
import os
import sys
import tempfile
from pathlib import Path

from agent_voice import models

DEFAULTS = {
    "enabled": False,
    "voice": "em_alex",
    "lang": "es-419",
    "model": "fp32",
    "speed": 1.0,
    "volume": 1.0,
    # Seconds a reply may stay pending for focus before it is dropped; 0 = no limit.
    "pending_max_wait_s": 0,
}


# Short language code -> engine language code.
LANG_CODES = {"es": "es-419", "en": "en-us"}
# Voices per short language code; validated statically so no model load is needed.
VOICES_BY_LANG = {"es": ("ef_dora", "em_alex", "em_santa"), "en": ("am_michael",)}
DEFAULT_VOICE_BY_LANG = {"es": "em_alex", "en": "am_michael"}
# Union of every language's voices, Spanish first.
VOICES = tuple(v for voices in VOICES_BY_LANG.values() for v in voices)
MIN_SPEED = 1.0
MAX_SPEED = 2.0
STEP = 0.25
# Playback volume multiplier for afplay -v: 0 = silent, 1 = normal, above 1 amplifies.
MIN_VOLUME = 0.0
MAX_VOLUME = 2.0
VOLUME_STEP = 0.1

_VALID = {
    "voice": lambda v: v in VOICES,
    "model": lambda v: isinstance(v, str) and v in models.VARIANTS,
    "speed": lambda v: (
        isinstance(v, (int, float))
        and not isinstance(v, bool)
        and math.isfinite(v)
        and MIN_SPEED <= v <= MAX_SPEED
    ),
    "volume": lambda v: (
        isinstance(v, (int, float))
        and not isinstance(v, bool)
        and math.isfinite(v)
        and MIN_VOLUME <= v <= MAX_VOLUME
    ),
    "pending_max_wait_s": lambda v: (
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0
    ),
    "lang": lambda v: isinstance(v, str) and bool(v),
    "claude_config_dirs": lambda v: isinstance(v, list) and all(isinstance(d, str) and d for d in v),
}


def config_path(env=None) -> Path:
    env = os.environ if env is None else env
    if env.get("AGENT_VOICE_HOME"):
        return Path(env["AGENT_VOICE_HOME"]) / "config.json"
    if env.get("XDG_CONFIG_HOME"):
        return Path(env["XDG_CONFIG_HOME"]) / "agent-voice" / "config.json"
    return Path.home() / ".config" / "agent-voice" / "config.json"


def _read(env) -> dict:
    try:
        data = json.loads(config_path(env).read_text())
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        print("agent-voice: config file is unreadable; using defaults", file=sys.stderr)
        return {}
    return data


def load(env=None) -> dict:
    """Defaults overlaid with the file; invalid values fall back with one warning."""
    data = _read(env)
    bad = [k for k, ok in _VALID.items() if k in data and not ok(data[k])]
    if bad:
        print(
            f"agent-voice: invalid config value for {', '.join(bad)}; using defaults",
            file=sys.stderr,
        )
        data = {k: v for k, v in data.items() if k not in bad}
    return {**DEFAULTS, **data}


def save(updates: dict, env=None) -> None:
    path = config_path(env)
    data = {**_read(env), **updates}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
