import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys

from agent_voice import __version__, config, focus, herdr, models, player, text, waiter


def _build_parser():
    parser = argparse.ArgumentParser(prog="agent-voice")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("on", help="enable automatic speaking")
    sub.add_parser("off", help="disable automatic speaking and stop playback")
    sub.add_parser("stop", help="stop the current utterance")
    sub.add_parser("toggle", help="flip automatic speaking (turning it off also stops playback)")
    sub.add_parser("status", help="show settings and whether model files are present")
    voice = sub.add_parser("voice", help="show or set the default voice")
    voice.add_argument("name", nargs="?")
    model = sub.add_parser("model", help="show or set the default model variant")
    model.add_argument("name", nargs="?")
    pending = sub.add_parser("pending-wait", help="show or set how long a reply may wait for focus")
    pending.add_argument("duration", nargs="?", metavar="DURATION|off")
    download = sub.add_parser("download", help="download model files (the only network path)")
    download.add_argument("--model", choices=sorted(models.VARIANTS))
    speak = sub.add_parser("speak", help="read a reply from stdin and speak it")
    speak.add_argument("--voice")
    speak.add_argument("--speed", type=float)
    speak.add_argument("--lang")
    speak.add_argument("--model", choices=sorted(models.VARIANTS))
    speak.add_argument("--detach", action="store_true")
    speak.add_argument("--always", action="store_true", help=argparse.SUPPRESS)
    speak.add_argument("--wait-focus", action="store_true", help=argparse.SUPPRESS)
    repeat = sub.add_parser("repeat", help="re-speak the last reply from the agent's transcript")
    repeat.add_argument("--config-dir", action="append", dest="config_dirs", metavar="DIR")
    repeat.add_argument("--voice")
    repeat.add_argument("--speed", type=float)
    repeat.add_argument("--lang")
    repeat.add_argument("--model", choices=sorted(models.VARIANTS))
    repeat.add_argument("--detach", action="store_true")
    keys = sub.add_parser("keys", help="print a hotkey snippet for a hotkey daemon (never writes files)")
    keys.add_argument("target")
    hook = sub.add_parser("hook", help="agent hook entry point (reads the agent's JSON on stdin)")
    hook.add_argument("agent", choices=["claude"])
    for name, helptext in (("install", "register the hook"), ("uninstall", "remove the hook")):
        cmd = sub.add_parser(name, help=f"{helptext} for an agent")
        cmd.add_argument("agent", choices=["claude"])
        cmd.add_argument("--config-dir", action="append", dest="config_dirs", metavar="DIR")
    return parser


def _cmd_on(args) -> int:
    config.save({"enabled": True})
    return 0


def _cmd_off(args) -> int:
    config.save({"enabled": False})
    player.stop()
    waiter.cancel_all()
    return 0


def _cmd_toggle(args) -> int:
    if config.load()["enabled"]:
        _cmd_off(args)
        print("disabled")
    else:
        _cmd_on(args)
        print("enabled")
    return 0


def _cmd_stop(args) -> int:
    player.stop()
    return 0


def _cmd_status(args) -> int:
    cfg = config.load()
    try:
        models.resolve(cfg["model"])
        files = "ok"
    except (models.ModelsMissingError, OSError, ValueError):
        files = "missing"
    print("enabled" if cfg["enabled"] else "disabled")
    print(f"voice: {cfg['voice']}")
    print(f"model: {cfg['model']}")
    print(f"speed: {cfg['speed']}")
    print(f"lang: {cfg['lang']}")
    print(f"pending wait: {_format_wait(cfg['pending_max_wait_s'])}")
    print(f"model files: {files}")
    return 0


VOICES = config.VOICES


def _cmd_voice(args) -> int:
    if args.name is not None:
        if args.name not in VOICES:
            print(
                f"agent-voice: unknown voice {args.name!r}; choose one of: {', '.join(VOICES)}",
                file=sys.stderr,
            )
            return 2
        config.save({"voice": args.name})
        return 0
    print(f"current: {config.load()['voice']}")
    print(f"available: {', '.join(VOICES)}")
    return 0


def _cmd_model(args) -> int:
    if args.name is not None:
        if args.name not in models.VARIANTS:
            print(
                f"agent-voice: unknown model {args.name!r}; choose one of: {', '.join(models.VARIANTS)}",
                file=sys.stderr,
            )
            return 2
        config.save({"model": args.name})
        try:
            models.resolve(args.name)
        except Exception:
            print(
                f"agent-voice: model files not downloaded; run: agent-voice download --model {args.name}",
                file=sys.stderr,
            )
        return 0
    print(f"current: {config.load()['model']}")
    print(f"available: {', '.join(models.VARIANTS)}")
    return 0


_UNITS = {"s": 1, "m": 60, "h": 3600}
_DURATION = re.compile(r"(\d+)([smh]?)")


def _format_wait(seconds) -> str:
    if not seconds:
        return "off"
    if seconds != int(seconds):
        return f"{seconds}s"
    seconds = int(seconds)
    for unit in ("h", "m"):
        if seconds % _UNITS[unit] == 0:
            return f"{seconds // _UNITS[unit]}{unit}"
    return f"{seconds}s"


def _cmd_pending_wait(args) -> int:
    if args.duration is None:
        print(_format_wait(config.load()["pending_max_wait_s"]))
        return 0
    match = None if args.duration == "off" else _DURATION.fullmatch(args.duration)
    if args.duration != "off" and match is None:
        print(
            f"agent-voice: invalid duration {args.duration!r}; use e.g. 45s, 30m, 2h or off",
            file=sys.stderr,
        )
        return 2
    seconds = 0 if match is None else int(match.group(1)) * _UNITS[match.group(2) or "s"]
    config.save({"pending_max_wait_s": seconds})
    return 0


def _progress(name, done, total) -> None:
    pct = f" {done * 100 // total}%" if total else ""
    print(f"\r{name}{pct}", end="", file=sys.stderr, flush=True)


def _cmd_download(args) -> int:
    variant = args.model or config.load()["model"]
    try:
        models.download(variant, progress_cb=_progress)
    except Exception as exc:
        print(f"\nagent-voice: download failed: {exc}", file=sys.stderr)
        return 1
    print(f"\nagent-voice: {variant} ready", file=sys.stderr)
    return 0


SAFE_CWD = "/"


def _spawn(args, raw: str, always: bool = False, wait_focus: bool = False) -> int:
    """Hand the text to a new-session child over a pipe (never touches disk); returns its pid."""
    # -P (3.11+) and cwd="/" keep the hook's cwd (a project dir) off the child's sys.path:
    # a `json.py` or `agent_voice/` there must never be imported (code execution).
    argv = [sys.executable, *(["-P"] if sys.version_info >= (3, 11) else []), "-m", "agent_voice", "speak"]
    for flag in ("voice", "speed", "lang", "model"):
        value = getattr(args, flag)
        if value is not None:
            argv += [f"--{flag}", str(value)]
    if always:
        argv.append("--always")
    if wait_focus:
        argv.append("--wait-focus")
    child = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        cwd=SAFE_CWD,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONSAFEPATH": "1"},
    )
    child.stdin.write(raw.encode("utf-8"))
    child.stdin.close()
    return child.pid


def _detach(args, raw: str, always: bool = False) -> int:
    _spawn(args, raw, always=always)
    return 0


def _detach_waiting(args, raw: str, pane_id: str) -> int:
    """Spawn a child that holds `raw` in memory until `pane_id` has focus, then speaks.

    The pane's previous waiter (if any) is terminated: a newer reply replaces the pending one.
    """
    pid = _spawn(args, raw, wait_focus=True)
    waiter.register(pane_id, pid)
    return 0


def _speak(args) -> int:
    cfg = config.load()
    if not cfg["enabled"] and not args.always:
        if args.detach or args.wait_focus:
            sys.stdin.read()  # drain, so the writer never blocks or gets EPIPE
        return 0
    raw = sys.stdin.read()  # always before waiting, so the parent's pipe write completes
    if args.wait_focus and not _await_focus(cfg):
        return 0
    return _speak_text(args, raw, cfg, always=args.always)


def _await_focus(cfg: dict) -> bool:
    """True when it is time to speak. Without a focus detector that is immediately."""
    detector = focus.detect_from_env(os.environ)
    if detector is None:
        return True
    try:
        return waiter.wait_for_focus(
            detector,
            enabled=lambda: config.load()["enabled"],
            max_wait=cfg["pending_max_wait_s"],
        )
    finally:
        waiter.release(detector.pane_id)


def _speak_text(args, raw: str, cfg: dict, always: bool = False) -> int:
    """Shared pipeline for `speak` and `repeat`; the caller decides the enabled flag.

    `always` is forwarded to a detached child so it also bypasses the enabled flag.
    """
    if args.detach:
        return _detach(args, raw, always=always)
    player.claim()  # before any text processing: `stop`/`off` can always cut this process
    try:
        return _synthesize_and_play(args, raw, cfg)
    finally:
        player.release()


def _synthesize_and_play(args, raw: str, cfg: dict) -> int:
    chunks = text.chunks(raw)
    if not chunks:
        return 0
    from agent_voice import engine

    model = args.model or cfg["model"]
    voice = args.voice or cfg["voice"]
    speed = cfg["speed"] if args.speed is None else args.speed
    lang = args.lang or cfg["lang"]
    try:
        model_path, voices_path = models.resolve(model)
    except models.ModelsMissingError:
        print(
            f"agent-voice: model files missing; run: agent-voice download --model {model}",
            file=sys.stderr,
        )
        return 0
    eng = engine.Engine(model_path, voices_path)

    def synth(chunk):
        samples, rate = eng.synthesize(chunk, voice=voice, speed=speed, lang=lang)
        return engine.to_wav_bytes(samples, rate)

    player.speak(chunks, synth)
    return 0


def _cmd_speak(args) -> int:
    """Never fails the agent: any error becomes a generic stderr line and exit 0."""
    try:
        return _speak(args)
    except Exception as exc:
        print(f"agent-voice: could not speak ({type(exc).__name__})", file=sys.stderr)
        return 0


def _herdr_pane():
    """The herdr pane the user means (HERDR_PANE_ID, else the focused one); None if unknown."""
    try:
        return herdr.current_pane(os.environ)
    except Exception:
        return None


def resolve_reply(dirs):
    """(strategy, reply) for `repeat`; reply is None when nothing was found.

    Strategies, in order: "session-id" (the herdr pane's Claude session transcript),
    "cwd" (that pane's project folder). A resolved herdr pane with neither yields
    ("no-pane-reply", None): never another conversation. Only when no pane is resolved
    (herdr unavailable or erroring) does "fallback" apply: this process's cwd, then the
    newest conversation overall.
    """
    from agent_voice.adapters import claude

    pane = _herdr_pane()
    if pane is not None:
        session_id = herdr.claude_session_id(pane)
        reply = claude.session_reply(dirs, session_id) if session_id else None
        if reply:
            return "session-id", reply
        pane_cwd = pane.get("cwd")
        reply = claude.project_reply(dirs, pane_cwd) if isinstance(pane_cwd, str) else None
        if reply:
            return "cwd", reply
        return "no-pane-reply", None
    return "fallback", claude.last_reply(dirs, cwd=os.getcwd())


def _cmd_repeat(args) -> int:
    """Re-speak the last reply read from the agent's own transcript.

    An explicit user command, so it speaks whether or not automatic speaking is
    enabled. Nothing is persisted; messages never include reply text.
    """
    from agent_voice.adapters import claude

    try:
        dirs = (
            args.config_dirs
            or config.load().get("claude_config_dirs")
            or [claude.default_config_dir()]
        )
        strategy, reply = resolve_reply(dirs)
        if reply is None:
            what = "for this pane" if strategy == "no-pane-reply" else "found"
            print(f"agent-voice: no previous reply {what}", file=sys.stderr)
            return 1
        return _speak_text(args, reply, config.load(), always=True)
    except Exception as exc:
        print(f"agent-voice: could not repeat ({type(exc).__name__})", file=sys.stderr)
        return 1


KEY_TARGETS = ("skhd",)


def _executable() -> str:
    """Absolute path of the agent-voice executable that is running (skhd has a minimal PATH)."""
    argv0 = sys.argv[0]
    if os.path.basename(argv0) == "agent-voice":
        return os.path.abspath(argv0)
    return shutil.which("agent-voice") or os.path.abspath(argv0)


def _cmd_keys(args) -> int:
    if args.target not in KEY_TARGETS:
        print(
            f"agent-voice: unknown target {args.target!r}; supported: {', '.join(KEY_TARGETS)}",
            file=sys.stderr,
        )
        return 2
    exe = shlex.quote(_executable())
    print("# agent-voice")
    print(f"ctrl + alt - q : {exe} stop")
    print(f"ctrl + alt - r : {exe} repeat --detach")
    print(f"ctrl + alt - v : {exe} toggle")
    return 0


def _cmd_hook(args) -> int:
    """Never fails the agent: always exits 0 and prints nothing to stdout."""
    from agent_voice.adapters import claude

    try:
        return claude.run_hook(sys.stdin.read())
    except Exception:
        return 0


def _report_partial(exc) -> int:
    """No rollback: say exactly which settings files changed and which failed."""
    print("agent-voice: some settings files could not be written", file=sys.stderr)
    for path in exc.changed:
        print(f"changed {path}", file=sys.stderr)
    for path, reason in exc.failed:
        print(f"failed {path}: {reason}", file=sys.stderr)
    return 1


def _claude_dirs(args) -> list:
    """Absolute, de-duplicated config dirs from the flags or the Claude default."""
    from agent_voice.adapters import claude

    raw = args.config_dirs or [claude.default_config_dir()]
    return list(dict.fromkeys(os.path.abspath(d) for d in raw))


def _remember_dirs(dirs, forget: bool) -> None:
    """Keep `claude_config_dirs` (used by `repeat`) in step with install/uninstall."""
    known = config.load().get("claude_config_dirs", [])
    kept = [d for d in known if d not in dirs] if forget else list(dict.fromkeys(known + dirs))
    if kept != known:
        config.save({"claude_config_dirs": kept})


def _run_claude(args, action: str) -> int:
    from agent_voice.adapters import claude

    dirs = _claude_dirs(args)
    try:
        results = getattr(claude, action)(dirs)
    except claude.SettingsError as exc:
        print(f"agent-voice: {exc}; nothing changed", file=sys.stderr)
        return 1
    except claude.PartialWriteError as exc:
        failed = {str(p.parent) for p, _ in exc.failed}
        _remember_dirs([d for d in dirs if d not in failed], forget=action == "uninstall")
        return _report_partial(exc)
    for path, changed in results:
        print(f"{'updated' if changed else 'unchanged'} {path}")
    _remember_dirs(dirs, forget=action == "uninstall")
    return 0


def _cmd_install(args) -> int:
    return _run_claude(args, "install")


def _cmd_uninstall(args) -> int:
    return _run_claude(args, "uninstall")


COMMANDS = {
    "repeat": _cmd_repeat,
    "uninstall": _cmd_uninstall,
    "install": _cmd_install,
    "hook": _cmd_hook,
    "speak": _cmd_speak,
    "download": _cmd_download,
    "model": _cmd_model,
    "voice": _cmd_voice,
    "on": _cmd_on,
    "off": _cmd_off,
    "stop": _cmd_stop,
    "toggle": _cmd_toggle,
    "keys": _cmd_keys,
    "pending-wait": _cmd_pending_wait,
    "status": _cmd_status,
}


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    if args.version:
        print(f"agent-voice {__version__}")
        return 0
    if args.command is None:
        return 0
    return COMMANDS[args.command](args)
