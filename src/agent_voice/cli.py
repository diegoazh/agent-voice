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
    except Exception:
        files = "missing"
    print("enabled" if cfg["enabled"] else "disabled")
    print(f"voice: {cfg['voice']}")
    print(f"model: {cfg['model']}")
    print(f"speed: {cfg['speed']}")
    print(f"lang: {cfg['lang']}")
    print(f"model files: {files}")
    return 0


# Spanish voices only; validated statically so no model load is needed.
VOICES = ("ef_dora", "em_alex", "em_santa")


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


def _detach(args, raw: str) -> int:
    """Hand the text to a new-session child over a pipe (never touches disk)."""
    argv = [sys.executable, "-m", "agent_voice", "speak"]
    for flag in ("voice", "speed", "lang", "model"):
        value = getattr(args, flag)
        if value is not None:
            argv += [f"--{flag}", str(value)]
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
    if not cfg["enabled"]:
        return 0
    raw = sys.stdin.read()
    if args.detach:
        return _detach(args, raw)
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


def _cmd_hook(args) -> int:
    """Never fails the agent: always exits 0 and prints nothing to stdout."""
    from agent_voice.adapters import claude

    try:
        return claude.run_hook(sys.stdin.read())
    except Exception:
        return 0


def _cmd_install(args) -> int:
    from agent_voice.adapters import claude

    try:
        results = claude.install(args.config_dirs or [claude.default_config_dir()])
    except claude.SettingsError as exc:
        print(f"agent-voice: {exc}; nothing changed", file=sys.stderr)
        return 1
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
    for path, changed in results:
        print(f"{'updated' if changed else 'unchanged'} {path}")
    return 0


COMMANDS = {
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
