"""Pipelined local playback for synthesized speech."""

import fcntl
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

QUEUE_SIZE = 2
POLL = 0.05
_DONE = object()


PID_FILE = "speaker.pid"
LOCK_FILE = "speaker.lock"
EXIT_WAIT = 1.0


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


def process_start_time(pid):
    """Start time of `pid` as reported by the OS (`ps`), or None if unavailable."""
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            env={**os.environ, "LC_ALL": "C"},
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = result.stdout.strip()
    return out if result.returncode == 0 and out else None


def process_state(pid):
    """State flags of `pid` as reported by `ps` (e.g. "S", "T"), or None if unavailable."""
    try:
        result = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            env={**os.environ, "LC_ALL": "C"},
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = result.stdout.strip()
    return out if result.returncode == 0 and out else None


def _signal(pid, sig, kill=None):
    """Signal the speaker's process group when it leads its own, else only the pid.

    Detached speakers lead their own group, so the `afplay` child is signalled too. A
    foreground speaker shares its shell's group: never signal that group.
    """
    kill = os.kill if kill is None else kill
    if os.getpgid(pid) == pid:
        os.killpg(pid, sig)
    else:
        kill(pid, sig)


def _read_record(directory, pid_file=PID_FILE):
    """Return (pid, start_time) from the PID file; either may be None."""
    try:
        lines = (Path(directory) / pid_file).read_text().splitlines()
        pid = int(lines[0].strip())
    except (OSError, ValueError, IndexError):
        return None, None
    start = lines[1].strip() if len(lines) > 1 and lines[1].strip() else None
    return pid, start


def _read_pid(directory, pid_file=PID_FILE):
    return _read_record(directory, pid_file)[0]


def _is_previous_speaker(pid, stored_start, start_time):
    """True only for a live process whose start time matches the recorded one."""
    if pid is None or pid <= 1 or pid == os.getpid() or stored_start is None or not _alive(pid):
        return False
    current = start_time(pid)
    return current is not None and current == stored_start


def _terminate_previous(directory, start_time, pid_file=PID_FILE):
    """SIGTERM a verified previous speaker and wait briefly for it to exit."""
    pid, stored_start = _read_record(directory, pid_file)
    if not _is_previous_speaker(pid, stored_start, start_time):
        return False
    try:
        _signal(pid, signal.SIGCONT)  # a paused speaker ignores SIGTERM until continued
    except OSError:
        pass
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False  # not ours to signal
    deadline = time.monotonic() + EXIT_WAIT
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.01)
    return True


def stop(directory=None, *, start_time=None, pid_file=PID_FILE):
    """Terminate the current speaker, if any. Returns True if one was running."""
    directory = runtime_dir() if directory is None else Path(directory)
    start_time = process_start_time if start_time is None else start_time
    stopped = _terminate_previous(directory, start_time, pid_file)
    pid, stored_start = _read_record(directory, pid_file)
    if pid is not None and not _is_previous_speaker(pid, stored_start, start_time):
        (directory / pid_file).unlink(missing_ok=True)
    return stopped


def pause(directory=None, *, start_time=None, state=None, kill=None, pid_file=PID_FILE):
    """Toggle pause on the current speaker. Returns True if a speaker was signalled."""
    directory = runtime_dir() if directory is None else Path(directory)
    start_time = process_start_time if start_time is None else start_time
    state = process_state if state is None else state
    pid, stored_start = _read_record(directory, pid_file)
    if not _is_previous_speaker(pid, stored_start, start_time):
        return False
    paused = (state(pid) or "").startswith("T")
    try:
        _signal(pid, signal.SIGCONT if paused else signal.SIGSTOP, kill)
    except OSError:  # gone, or not ours to signal
        return False
    return True


def _claim(directory, start_time, pid=None, pid_file=PID_FILE, lock_file=LOCK_FILE):
    """Make `pid` (default: this process) the owner of `pid_file`, ending the previous owner."""
    pid = os.getpid() if pid is None else pid
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Serialize concurrent starters so exactly one ends up owning the PID file.
    with open(directory / lock_file, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _terminate_previous(directory, start_time, pid_file)
        own = start_time(pid)
        content = str(pid) if own is None else f"{pid}\n{own}"
        tmp = directory / f"{pid_file}.{os.getpid()}.tmp"
        tmp.write_text(content)
        os.replace(tmp, directory / pid_file)


def _release(directory, pid_file=PID_FILE):
    if _read_pid(directory, pid_file) == os.getpid():
        try:
            (Path(directory) / pid_file).unlink()
        except FileNotFoundError:
            pass


TMP_PREFIX = "agent-voice-"
STALE_AFTER_S = 600
_TMP_NAME = re.compile(rf"{re.escape(TMP_PREFIX)}(\d+)-")


def _sweep_stale(workdir=None, *, now=None):
    """Remove leftover temp dirs of agent-voice processes that no longer exist.

    Conservative: only real directories (never symlinks) owned by this user, named
    `agent-voice-<pid>-*`, whose pid is not alive and whose last change is older than
    STALE_AFTER_S. Best effort: any error is ignored.
    """
    root = tempfile.gettempdir() if workdir is None else str(workdir)
    now = time.time() if now is None else now
    try:
        with os.scandir(root) as entries:
            found = list(entries)
        for entry in found:
            match = _TMP_NAME.match(entry.name)
            if not match or not entry.is_dir(follow_symlinks=False):  # symlinks never count
                continue
            info = entry.stat(follow_symlinks=False)
            pid = int(match.group(1))
            if info.st_uid != os.getuid() or now - info.st_mtime < STALE_AFTER_S:
                continue
            if pid == os.getpid() or _alive(pid):
                continue
            shutil.rmtree(entry.path, ignore_errors=True)
    except OSError:
        pass


def claim(run_dir=None, start_time=None):
    """Register this process as the speaker now (ending the previous one).

    Called before the reply text is processed, so `stop`/`off` can always cut this
    process; `speak` claims again (idempotent) and releases on exit.
    """
    _claim(
        runtime_dir() if run_dir is None else Path(run_dir),
        process_start_time if start_time is None else start_time,
    )


def release(run_dir=None):
    """Drop this process's own PID record, if it still owns it."""
    _release(runtime_dir() if run_dir is None else Path(run_dir))


def speak(chunks, synth, *, play=None, workdir=None, run_dir=None, handle_signals=True,
          start_time=None):
    """Synthesize and play `chunks` in order, synthesizing ahead of playback.

    `synth(chunk)` returns WAV bytes; `play(path)` plays a WAV file and blocks.
    A chunk whose synthesis fails is skipped (reported without its text).
    Returns True when every chunk was handled, False when interrupted by SIGTERM.
    Errors raised by `play` propagate after cleanup.
    """
    run_dir = runtime_dir() if run_dir is None else Path(run_dir)
    play = AfplayPlayer() if play is None else play
    start_time = process_start_time if start_time is None else start_time
    stop = threading.Event()
    ready = queue.Queue(maxsize=QUEUE_SIZE)
    tmp = None
    producer = None
    previous_handler = None
    in_main = handle_signals and threading.current_thread() is threading.main_thread()

    def on_sigterm(signum, frame):
        stop.set()
        terminate = getattr(play, "terminate", None)
        if terminate is not None:
            terminate()

    def put(item):
        # Bounded wait so an aborted consumer can never leave us blocked.
        while not stop.is_set():
            try:
                ready.put(item, timeout=POLL)
                return
            except queue.Full:
                pass

    def produce():
        for i, chunk in enumerate(chunks):
            if stop.is_set():
                return
            try:
                wav = synth(chunk)
            except Exception:
                # Never include the chunk text or the exception message.
                print("agent-voice: synthesis failed for a chunk; skipped", file=sys.stderr)
                continue
            put((i, wav))
        put(_DONE)

    def next_item():
        while not stop.is_set():
            try:
                return ready.get(timeout=POLL)
            except queue.Empty:
                pass
        return _DONE

    try:
        if in_main:
            previous_handler = signal.signal(signal.SIGTERM, on_sigterm)
        _claim(run_dir, start_time)
        _sweep_stale(workdir)
        tmp = tempfile.mkdtemp(prefix=f"{TMP_PREFIX}{os.getpid()}-", dir=workdir)
        tmp = os.path.abspath(tmp)
        producer = threading.Thread(target=produce, daemon=True)
        producer.start()
        while (item := next_item()) is not _DONE:
            index, wav = item
            path = os.path.join(tmp, f"{index}.wav")
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(wav)
                play(path)
            finally:
                _remove(path)
        return not stop.is_set()
    finally:
        stop.set()
        _release(run_dir)
        if producer is not None:
            producer.join(timeout=1)
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
        if in_main:
            signal.signal(signal.SIGTERM, previous_handler)


class AfplayPlayer:
    """Play WAV files with macOS `afplay`; `terminate()` can interrupt it."""

    def __init__(self, command=("afplay",), volume=1.0):
        self._command = list(command)
        self._volume = volume
        # Re-entrant: the SIGTERM handler calls terminate() on the main thread, possibly
        # while that same thread holds the lock inside __call__ (a plain Lock deadlocks).
        self._lock = threading.RLock()
        self._proc = None
        self._terminated = False

    def __call__(self, path):
        with self._lock:
            if self._terminated:
                return
            proc = self._proc = subprocess.Popen(
                [*self._command, "-v", f"{self._volume:g}", path]
            )
            if self._terminated:  # the handler ran during the spawn, before _proc was set
                proc.terminate()
        proc.wait()

    def terminate(self):
        with self._lock:
            self._terminated = True
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()


def runtime_dir(env=None):
    """Directory holding the speaker PID file (never any text)."""
    env = os.environ if env is None else env
    if env.get("AGENT_VOICE_HOME"):
        return Path(env["AGENT_VOICE_HOME"]) / "run"
    if env.get("XDG_STATE_HOME"):
        return Path(env["XDG_STATE_HOME"]) / "agent-voice"
    return Path.home() / ".local" / "state" / "agent-voice"


def _remove(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
