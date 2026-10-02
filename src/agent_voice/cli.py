import argparse
import os
import subprocess
import sys

from agent_voice import __version__, config, models, player, text


def _build_parser():
    parser = argparse.ArgumentParser(prog="agent-voice")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("on", help="enable automatic speaking")
    sub.add_parser("off", help="disable automatic speaking and stop playback")
    sub.add_parser("stop", help="stop the current utterance")
    sub.add_parser("status", help="show settings and whether model files are present")
    voice = sub.add_parser("voice", help="show or set the default voice")
    voice.add_argument("name", nargs="?")
    model = sub.add_parser("model", help="show or set the default model variant")
    model.add_argument("name", nargs="?")
    download = sub.add_parser("download", help="download model files (the only network path)")
    download.add_argument("--model", choices=sorted(models.VARIANTS))
    speak = sub.add_parser("speak", help="read a reply from stdin and speak it")
    speak.add_argument("--voice")
    speak.add_argument("--speed", type=float)
    speak.add_argument("--lang")
    speak.add_argument("--model", choices=sorted(models.VARIANTS))
    speak.add_argument("--detach", action="store_true")
    speak.add_argument("--always", action="store_true", help=argparse.SUPPRESS)
    repeat = sub.add_parser("repeat", help="re-speak the last reply from the agent's transcript")
    repeat.add_argument("--config-dir", action="append", dest="config_dirs", metavar="DIR")
    repeat.add_argument("--voice")
    repeat.add_argument("--speed", type=float)
    repeat.add_argument("--lang")
    repeat.add_argument("--model", choices=sorted(models.VARIANTS))
    repeat.add_argument("--detach", action="store_true")
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


def _detach(args, raw: str, always: bool = False) -> int:
    """Hand the text to a new-session child over a pipe (never touches disk)."""
    argv = [sys.executable, "-m", "agent_voice", "speak"]
    for flag in ("voice", "speed", "lang", "model"):
        value = getattr(args, flag)
        if value is not None:
            argv += [f"--{flag}", str(value)]
    if always:
        argv.append("--always")
    child = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    child.stdin.write(raw.encode("utf-8"))
    child.stdin.close()
    return 0


def _speak(args) -> int:
    cfg = config.load()
    if not cfg["enabled"] and not args.always:
        if args.detach:
            sys.stdin.read()  # drain, so the writer never blocks or gets EPIPE
        return 0
    return _speak_text(args, sys.stdin.read(), cfg, always=args.always)


def _speak_text(args, raw: str, cfg: dict, always: bool = False) -> int:
    """Shared pipeline for `speak` and `repeat`; the caller decides the enabled flag.

    `always` is forwarded to a detached child so it also bypasses the enabled flag.
    """
    if args.detach:
        return _detach(args, raw, always=always)
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


def _cmd_repeat(args) -> int:
    """Re-speak the last reply read from the agent's own transcript.

    An explicit user command, so it speaks whether or not automatic speaking is
    enabled. Nothing is persisted; messages never include reply text.
    """
    from agent_voice.adapters import claude

    try:
        dirs = args.config_dirs or [claude.default_config_dir()]
        reply = claude.last_reply(dirs, cwd=os.getcwd())
        if reply is None:
            print("agent-voice: no previous reply found", file=sys.stderr)
            return 1
        return _speak_text(args, reply, config.load(), always=True)
    except Exception as exc:
        print(f"agent-voice: could not repeat ({type(exc).__name__})", file=sys.stderr)
        return 1


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


def _cmd_install(args) -> int:
    from agent_voice.adapters import claude

    try:
        results = claude.install(args.config_dirs or [claude.default_config_dir()])
    except claude.SettingsError as exc:
        print(f"agent-voice: {exc}; nothing changed", file=sys.stderr)
        return 1
    except claude.PartialWriteError as exc:
        return _report_partial(exc)
    for path, changed in results:
        print(f"{'updated' if changed else 'unchanged'} {path}")
    return 0


def _cmd_uninstall(args) -> int:
    from agent_voice.adapters import claude

    try:
        results = claude.uninstall(args.config_dirs or [claude.default_config_dir()])
    except claude.SettingsError as exc:
        print(f"agent-voice: {exc}; nothing changed", file=sys.stderr)
        return 1
    except claude.PartialWriteError as exc:
        return _report_partial(exc)
    for path, changed in results:
        print(f"{'updated' if changed else 'unchanged'} {path}")
    return 0


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
