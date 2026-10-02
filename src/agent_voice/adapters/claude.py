"""Claude Code Stop-hook adapter."""

import argparse
import json


def run_hook(raw: str) -> int:
    from agent_voice import cli, config

    payload = json.loads(raw)
    if payload.get("stop_hook_active"):
        return 0
    if not config.load()["enabled"]:
        return 0
    message = payload.get("last_assistant_message")
    if not isinstance(message, str):
        return 0
    opts = argparse.Namespace(voice=None, speed=None, lang=None, model=None)
    return cli._detach(opts, message)
