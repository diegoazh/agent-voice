import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys

from agent_voice import __version__, config, focus, herdr, lang, models, player, text, waiter


def _build_parser():
    parser = argparse.ArgumentParser(prog="agent-voice")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("on", help="enable automatic speaking")
    sub.add_parser("off", help="disable automatic speaking and stop playback")
    sub.add_parser("stop", help="stop the current utterance")
    sub.add_parser("pause", help="pause or resume the current utterance")
    sub.add_parser("toggle", help="flip automatic speaking (turning it off also stops playback)")
    sub.add_parser("status", help="show settings and whether model files are present")
    voice = sub.add_parser("voice", help="show or set the default voice")
    voice.add_argument("name", nargs="?")
    lang = sub.add_parser("lang", help="show or switch the reading language (es/en)")
    lang.add_argument("choice", nargs="?", metavar="es|en")
    model = sub.add_parser("model", help="show or set the default model variant")
    model.add_argument("name", nargs="?")
    speed = sub.add_parser(
        "speed",
        help=f"show the reading speed, or step it up/down by {config.STEP} "
        f"({config.MIN_SPEED}-{config.MAX_SPEED})",
    )
    speed.add_argument("direction", nargs="?", metavar="up|down")
    volume = sub.add_parser(
        "volume",
        help=f"show the playback volume, or step it up/down by {config.VOLUME_STEP} "
        f"({config.MIN_VOLUME}-{config.MAX_VOLUME})",
    )
    volume.add_argument("direction", nargs="?", metavar="up|down")
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
    repeat = sub.add_parser("repeat", help="re-speak a reply from the agent's transcript (default: the last)")
    repeat.add_argument("n", nargs="?", type=int, default=1, metavar="N",
                        help="how many replies back (1 = last)")
    repeat.add_argument("--config-dir", action="append", dest="config_dirs", metavar="DIR")
    repeat.add_argument("--voice")
    repeat.add_argument("--speed", type=float)
    repeat.add_argument("--lang")
    repeat.add_argument("--model", choices=sorted(models.VARIANTS))
    repeat.add_argument("--detach", action="store_true")
    clip = sub.add_parser("say-clipboard", help="speak the current clipboard contents")
    clip.add_argument("--voice")
    clip.add_argument("--speed", type=float)
    clip.add_argument("--lang")
    clip.add_argument("--model", choices=sorted(models.VARIANTS))
    clip.add_argument("--detach", action="store_true")
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


def _cmd_pause(args) -> int:
    player.pause()
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
    print(f"volume: {cfg['volume']}")
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


def _cmd_lang(args) -> int:
    cfg = config.load()
    if args.choice is None:
        reverse = {code: short for short, code in config.LANG_CODES.items()}
        print(f"current: {reverse.get(cfg['lang'], cfg['lang'])}")
        print(f"available: {', '.join(config.LANG_CODES)}")
        return 0
    if args.choice not in config.LANG_CODES:
        print(
            f"agent-voice: unknown language {args.choice!r}; "
            f"choose one of: {', '.join(config.LANG_CODES)}",
            file=sys.stderr,
        )
        return 2
    changes = {"lang": config.LANG_CODES[args.choice]}
    if cfg["voice"] not in config.VOICES_BY_LANG[args.choice]:
        changes["voice"] = config.DEFAULT_VOICE_BY_LANG[args.choice]
    config.save(changes)
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


def _cmd_volume(args) -> int:
    current = config.load()["volume"]
    if args.direction is None:
        print(current)
        return 0
    if args.direction not in ("up", "down"):
        print("agent-voice: use: agent-voice volume [up|down]", file=sys.stderr)
        return 2
    delta = config.VOLUME_STEP if args.direction == "up" else -config.VOLUME_STEP
    new = round(min(max(current + delta, config.MIN_VOLUME), config.MAX_VOLUME), 2)
    config.save({"volume": new})
    print(new)
    return 0


def _cmd_speed(args) -> int:
    current = config.load()["speed"]
    if args.direction is None:
        print(current)
        return 0
    if args.direction not in ("up", "down"):
        print("agent-voice: use: agent-voice speed [up|down]", file=sys.stderr)
        return 2
    delta = config.STEP if args.direction == "up" else -config.STEP
    new = round(min(max(current + delta, config.MIN_SPEED), config.MAX_SPEED), 2)
    config.save({"speed": new})
    print(new)
    return 0


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


def _short_lang(code):
    """Map a full engine lang code (es-419) to its short code (es); None if unknown."""
    for short, full in config.LANG_CODES.items():
        if full == code:
            return short
    return None


def _voice_for(detected: str, base_voice: str) -> str:
    """Keep `base_voice` when it belongs to `detected`, else that language's default."""
    if base_voice in config.VOICES_BY_LANG.get(detected, ()):
        return base_voice
    return config.DEFAULT_VOICE_BY_LANG[detected]


def _language_runs(raw: str, previous):
    """Yield (run_text, short_lang) for consecutive blocks sharing a detected language.

    `previous` seeds the first detection and is carried forward, so a low-signal
    block keeps the language of the block before it. Fenced code is ignored for
    detection (it is not read aloud), so a code block inherits the surrounding
    language and its spoken placeholder matches. Consecutive blocks with the same
    language are joined into one run so their chunks merge as before.
    """
    runs: list[list[str]] = []
    run_langs: list[str] = []
    for block in text.split_blocks(raw):
        previous = lang.detect_lang(text.without_fenced_code(block), previous)
        if run_langs and run_langs[-1] == previous:
            runs[-1].append(block)
        else:
            runs.append([block])
            run_langs.append(previous)
    for blocks, short in zip(runs, run_langs):
        yield "\n\n".join(blocks), short


def _plan_chunks(args, raw: str, cfg: dict):
    """Return (chunk_texts, plans) where plans[i] is the (voice, engine_lang) for chunk i.

    With an explicit --lang the whole reply keeps one voice/lang and no detection
    runs, so existing callers are unchanged. Without --lang, each block's language
    is detected (seeded from the configured language and carried forward), and each
    run is normalized and chunked in its own language.
    """
    base_voice = args.voice or cfg["voice"]
    if args.lang is not None:
        short = _short_lang(args.lang) or text.DEFAULT_LANG
        chunk_texts = text.chunks(raw, lang=short)
        return chunk_texts, [(base_voice, args.lang)] * len(chunk_texts)
    chunk_texts: list[str] = []
    plans: list[tuple[str, str]] = []
    for run_text, short in _language_runs(raw, _short_lang(cfg["lang"])):
        voice = _voice_for(short, base_voice)
        engine_lang = config.LANG_CODES[short]
        run_chunks = text.chunks(run_text, lang=short)
        chunk_texts.extend(run_chunks)
        plans.extend([(voice, engine_lang)] * len(run_chunks))
    return chunk_texts, plans


def _synthesize_and_play(args, raw: str, cfg: dict) -> int:
    chunk_texts, plans = _plan_chunks(args, raw, cfg)
    if not chunk_texts:
        return 0
    from agent_voice import engine

    model = args.model or cfg["model"]
    speed = cfg["speed"] if args.speed is None else args.speed
    try:
        model_path, voices_path = models.resolve(model)
    except models.ModelsMissingError:
        print(
            f"agent-voice: model files missing; run: agent-voice download --model {model}",
            file=sys.stderr,
        )
        return 0
    eng = engine.Engine(model_path, voices_path)
    plan_iter = iter(plans)

    def synth(chunk):
        voice, lang_code = next(plan_iter)
        samples, rate = eng.synthesize(chunk, voice=voice, speed=speed, lang=lang_code)
        return engine.to_wav_bytes(samples, rate)

    player.speak(chunk_texts, synth, play=player.AfplayPlayer(volume=cfg["volume"]))
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


def resolve_reply(dirs, nth=1):
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
        reply = claude.session_reply(dirs, session_id, nth=nth) if session_id else None
        if reply:
            return "session-id", reply
        pane_cwd = pane.get("cwd")
        reply = claude.project_reply(dirs, pane_cwd, nth=nth) if isinstance(pane_cwd, str) else None
        if reply:
            return "cwd", reply
        return "no-pane-reply", None
    return "fallback", claude.last_reply(dirs, cwd=os.getcwd(), nth=nth)


def _cmd_repeat(args) -> int:
    """Re-speak the last reply read from the agent's own transcript.

    An explicit user command, so it speaks whether or not automatic speaking is
    enabled. Nothing is persisted; messages never include reply text.
    """
    from agent_voice.adapters import claude

    if args.n < 1:
        print("agent-voice: repeat index must be 1 or greater", file=sys.stderr)
        return 2
    try:
        dirs = (
            args.config_dirs
            or config.load().get("claude_config_dirs")
            or [claude.default_config_dir()]
        )
        strategy, reply = resolve_reply(dirs, nth=args.n)
        if reply is None:
            what = "for this pane" if strategy == "no-pane-reply" else "found"
            print(f"agent-voice: no previous reply {what}", file=sys.stderr)
            return 1
        return _speak_text(args, reply, config.load(), always=True)
    except Exception as exc:
        print(f"agent-voice: could not repeat ({type(exc).__name__})", file=sys.stderr)
        return 1


def _clipboard_text():
    """The macOS clipboard text via `pbpaste`, or None if it could not be read."""
    try:
        result = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def _cmd_say_clipboard(args) -> int:
    """Speak the clipboard. An explicit user command, so it ignores the enabled flag.

    The clipboard is user content: it only flows into the speech pipeline, never to
    disk, stdout or stderr; messages are static.
    """
    clip = _clipboard_text()
    if clip is None or not clip.strip():
        print("agent-voice: nothing in the clipboard to speak", file=sys.stderr)
        return 1
    return _speak_text(args, clip, config.load(), always=True)


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
    print(f"ctrl + alt - p : {exe} pause")
    print(f"ctrl + alt - c : {exe} say-clipboard --detach")
    print(f"ctrl + alt - right : {exe} speed up")
    print(f"ctrl + alt - left : {exe} speed down")
    print(f"ctrl + alt - up : {exe} volume up")
    print(f"ctrl + alt - down : {exe} volume down")
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
    "say-clipboard": _cmd_say_clipboard,
    "uninstall": _cmd_uninstall,
    "install": _cmd_install,
    "hook": _cmd_hook,
    "speak": _cmd_speak,
    "download": _cmd_download,
    "model": _cmd_model,
    "voice": _cmd_voice,
    "lang": _cmd_lang,
    "speed": _cmd_speed,
    "volume": _cmd_volume,
    "on": _cmd_on,
    "off": _cmd_off,
    "stop": _cmd_stop,
    "pause": _cmd_pause,
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


def run(argv=None) -> int:
    code = main(argv)
    # agent-voice runs as many short-lived processes (one per spoken reply). When a
    # process loaded the TTS engine (kokoro_onnx -> onnxruntime), onnxruntime's bundled
    # telemetry tears down in C++ static destructors at interpreter exit; a lingering
    # telemetry worker thread then locks an already-destroyed recursive_mutex, throws an
    # uncaught std::system_error, and aborts — macOS logs a crash report every time.
    # os._exit() ends the process without running those destructors. It is safe here:
    # main() has already returned, so every finally/cleanup (e.g. player.release) has run,
    # and the project registers no atexit handlers. Only hard-exit when the engine was
    # actually loaded, so non-synthesis commands keep their normal, clean shutdown.
    #
    # Flushing must never skip the hard exit below: a closed downstream pipe makes
    # flush() raise BrokenPipeError, and if that propagated, the onnxruntime teardown
    # abort this function exists to prevent would happen anyway.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    if "onnxruntime" in sys.modules:
        os._exit(code)
    return code
