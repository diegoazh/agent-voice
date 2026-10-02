"""Claude Code Stop-hook adapter."""

import argparse
import glob
import json
import os
import re
import shlex
import shutil
import tempfile
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
    """Yield the file's lines (bytes) last to first, reading backwards in blocks.

    Linear in the file size: the partial line carried across blocks is kept as
    a list of chunks and joined once, when its start is found.
    """
    with open(path, "rb") as fh:
        pos = fh.seek(0, os.SEEK_END)
        pending = []  # chunks of the current line, newest file position first
        while pos > 0:
            step = min(_BLOCK, pos)
            pos -= step
            fh.seek(pos)
            parts = fh.read(step).split(b"\n")
            if len(parts) == 1:
                pending.append(parts[0])
                continue
            yield parts[-1] + b"".join(reversed(pending))
            yield from reversed(parts[1:-1])
            pending = [parts[0]]
        yield b"".join(reversed(pending))


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


def _session_focused(detector):
    """True/False, or None when unknown. An unknown focus must never silence a reply."""
    try:
        return detector.is_focused()
    except Exception:  # includes PaneGoneError: a vanished pane is simply not gating
        return None


def run_hook(raw: str) -> int:
    """Speak the reply now, or hold it in memory until its session has focus.

    With a focus detector (Ghostty + herdr): focused or unknown -> speak now; not focused
    -> a detached waiter keeps the text in memory and speaks when the pane gains focus.
    Either way, a newer reply for the pane replaces any pending one. No detector -> speak.
    """
    from agent_voice import cli, config, focus, waiter

    payload = json.loads(raw)
    if payload.get("stop_hook_active"):
        return 0
    if not config.load()["enabled"]:
        return 0
    message = payload.get("last_assistant_message")
    if not isinstance(message, str):
        return 0
    opts = argparse.Namespace(voice=None, speed=None, lang=None, model=None)
    detector = focus.detect_from_env(os.environ)
    if detector is None:
        return cli._detach(opts, message)
    if _session_focused(detector) is False:
        return cli._detach_waiting(opts, message, detector.pane_id)
    waiter.cancel(detector.pane_id)
    return cli._detach(opts, message)


HOOK_ARGS = " hook claude"
HOOK_NAME = "agent-voice"


def default_config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def hook_command() -> str:
    exe = shutil.which(HOOK_NAME) or HOOK_NAME
    return shlex.quote(exe) + HOOK_ARGS


def _is_ours(hook) -> bool:
    """Recognises both quoted commands and older unquoted ones."""
    if not isinstance(hook, dict):
        return False
    command = str(hook.get("command", ""))
    if not command.endswith(HOOK_ARGS):
        return False
    return command[: -len(HOOK_ARGS)].rstrip("'\"").endswith(HOOK_NAME)


class SettingsError(Exception):
    """A settings.json cannot be read or edited safely; the message names the file."""


class PartialWriteError(Exception):
    """Some settings files were written and others failed (no rollback)."""

    def __init__(self, changed, failed):
        self.changed = changed
        self.failed = failed
        super().__init__("could not write every settings file")


def _load(path: Path) -> dict:
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise SettingsError(f"{path} cannot be read ({exc.strerror or type(exc).__name__})") from exc
    except ValueError as exc:
        raise SettingsError(f"{path} is not valid JSON") from exc
    try:
        settings = json.loads(raw)
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
    """Atomically replace the real file (a symlink is written through, not replaced)."""
    real = Path(os.path.realpath(path))
    real.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_name(path.name + ".agent-voice.bak")
    if real.exists() and not backup.exists():
        shutil.copy2(real, backup)
    fd, tmp = tempfile.mkstemp(dir=real.parent, prefix=real.name + ".", suffix=".agent-voice.tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(settings, indent=2) + "\n")
        if real.exists():
            shutil.copymode(real, tmp)
        os.replace(tmp, real)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _apply(config_dirs, edit) -> list:
    """Load and edit every settings.json first, so an invalid one changes nothing.

    Writing is not transactional: if a write fails, files already written stay
    written; PartialWriteError reports which were changed and which failed.
    """
    plans = []
    for d in config_dirs:
        path = Path(d) / "settings.json"
        settings = _load(path)
        before = json.dumps(settings)
        try:
            edit(settings)
        except SettingsError as exc:
            raise SettingsError(f"{path}: {exc}") from exc
        plans.append((path, settings, json.dumps(settings) != before))
    done, failed = [], []
    for path, settings, changed in plans:
        if not changed:
            continue
        try:
            _write(path, settings)
            done.append(path)
        except OSError as exc:
            failed.append((path, exc.strerror or type(exc).__name__))
    if failed:
        raise PartialWriteError(done, failed)
    return [(path, changed) for path, _, changed in plans]


def install(config_dirs) -> list:
    """Return (path, changed) for each config dir; raise SettingsError before any write."""
    command = hook_command()
    return _apply(config_dirs, lambda s: _plan_install(s, command))


def _plan_uninstall(settings: dict) -> dict:
    """Remove our entry; delete only the containers that this removal emptied."""
    hooks = settings.get("hooks")
    stop = hooks.get("Stop") if isinstance(hooks, dict) else None
    if not isinstance(stop, list):
        return settings
    kept = []
    removed = False
    for group in stop:
        inner = group.get("hooks") if isinstance(group, dict) else None
        if not isinstance(inner, list) or not any(_is_ours(h) for h in inner):
            kept.append(group)
            continue
        removed = True
        group["hooks"] = [h for h in inner if not _is_ours(h)]
        if group["hooks"]:
            kept.append(group)
    if not removed:
        return settings
    if kept:
        hooks["Stop"] = kept
    else:
        del hooks["Stop"]
        if not hooks:
            del settings["hooks"]
    return settings


def uninstall(config_dirs) -> list:
    return _apply(config_dirs, _plan_uninstall)
