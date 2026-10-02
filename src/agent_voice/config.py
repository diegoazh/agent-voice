"""Persistent settings (JSON). Never holds reply text."""

import json
import os
import sys
import tempfile
from pathlib import Path

DEFAULTS = {
    "enabled": False,
    "voice": "em_alex",
    "lang": "es-419",
    "model": "fp32",
    "speed": 1.0,
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
    return {**DEFAULTS, **_read(env)}


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
