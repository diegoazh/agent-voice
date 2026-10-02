"""Claude Code Stop-hook adapter."""

import argparse
import glob
import json
import os
import re
import shutil
from pathlib import Path


def encode_project_dir(cwd) -> str:
    """Claude Code names a project folder after its cwd, non-alphanumerics -> '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(cwd))


def _newest_session(config_dirs, cwd=None):
    """Newest session file, preferring the project folder of `cwd` when it exists."""
    pattern = "projects/*/*.jsonl"
    if cwd is not None:
        pattern = f"projects/{glob.escape(encode_project_dir(cwd))}/*.jsonl"
    for pattern in dict.fromkeys([pattern, "projects/*/*.jsonl"]):
        files = [f for d in config_dirs for f in Path(d).glob(pattern)]
        if files:
            return max(files, key=lambda f: f.stat().st_mtime)
    return None


def _reply_text(entry):
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return None
    parts = [
        b["text"]
        for b in content
        if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
    ]
    return "\n".join(parts) if parts else None


_BLOCK = 64 * 1024


def _reverse_lines(path):
    """Yield the file's lines (bytes) last to first, reading backwards in blocks."""
    with open(path, "rb") as fh:
        pos = fh.seek(0, os.SEEK_END)
        tail = b""
        while pos > 0:
            step = min(_BLOCK, pos)
            pos -= step
            fh.seek(pos)
            lines = (fh.read(step) + tail).split(b"\n")
            tail = lines[0]
            yield from reversed(lines[1:])
        yield tail


def last_reply(config_dirs, cwd=None):
    """Final assistant text of the newest Claude Code session, or None.

    Read-only. Rule: scanning the session from the end, the first non-sidechain
    assistant entry that has at least one `text` block; its `text` blocks are
    joined with newlines (`thinking` and `tool_use` blocks are skipped, and
    earlier assistant entries are ignored), matching Stop's
    `last_assistant_message`. Malformed lines are skipped.
    """
    session = _newest_session(config_dirs, cwd)
    if session is None:
        return None
    for line in _reverse_lines(session):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if (
            isinstance(entry, dict)
            and entry.get("type") == "assistant"
            and not entry.get("isSidechain")
        ):
            reply = _reply_text(entry)
            if reply:
                return reply
    return None


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


HOOK_SUFFIX = "agent-voice hook claude"


def default_config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def hook_command() -> str:
    exe = shutil.which("agent-voice") or "agent-voice"
    return f"{exe} hook claude"


def _is_ours(hook) -> bool:
    return isinstance(hook, dict) and str(hook.get("command", "")).endswith(HOOK_SUFFIX)


class SettingsError(Exception):
    """settings.json exists but is not a JSON object."""


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        settings = json.loads(path.read_text())
    except ValueError as exc:
        raise SettingsError(f"{path} is not valid JSON") from exc
    if not isinstance(settings, dict):
        raise SettingsError(f"{path} is not a JSON object")
    return settings


def _plan_install(settings: dict, command: str) -> dict:
    hooks = settings.setdefault("hooks", {})
    stop = hooks.setdefault("Stop", []) if isinstance(hooks, dict) else None
    if not isinstance(stop, list):
        raise SettingsError("hooks.Stop has an unexpected shape")
    ours = [
        h
        for group in stop
        if isinstance(group, dict) and isinstance(group.get("hooks"), list)
        for h in group["hooks"]
        if _is_ours(h)
    ]
    if ours:
        for h in ours:
            h["command"] = command
    else:
        stop.append({"hooks": [{"type": "command", "command": command}]})
    return settings


def _write(path: Path, settings: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_name(path.name + ".agent-voice.bak")
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)
    tmp = path.with_name(path.name + ".agent-voice.tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n")
    os.replace(tmp, path)


def _apply(config_dirs, edit) -> list:
    """Load and edit every settings.json first, so an invalid one changes nothing."""
    plans = []
    for d in config_dirs:
        path = Path(d) / "settings.json"
        settings = _load(path)
        before = json.dumps(settings)
        plans.append((path, edit(settings), json.dumps(settings) != before))
    for path, settings, changed in plans:
        if changed:
            _write(path, settings)
    return [(path, changed) for path, _, changed in plans]


def install(config_dirs) -> list:
    """Return (path, changed) for each config dir; raise SettingsError before any write."""
    command = hook_command()
    return _apply(config_dirs, lambda s: _plan_install(s, command))


def _plan_uninstall(settings: dict) -> dict:
    hooks = settings.get("hooks")
    stop = hooks.get("Stop") if isinstance(hooks, dict) else None
    if not isinstance(stop, list):
        return settings
    kept = []
    for group in stop:
        inner = group.get("hooks") if isinstance(group, dict) else None
        if not isinstance(inner, list) or not any(_is_ours(h) for h in inner):
            kept.append(group)
            continue
        group["hooks"] = [h for h in inner if not _is_ours(h)]
        if group["hooks"]:
            kept.append(group)
    if kept:
        hooks["Stop"] = kept
    else:
        del hooks["Stop"]
        if not hooks:
            del settings["hooks"]
    return settings


def uninstall(config_dirs) -> list:
    return _apply(config_dirs, _plan_uninstall)
