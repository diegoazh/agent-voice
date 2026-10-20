# Philosophy

agent-voice is local text-to-speech for coding agents. This page states what the project
commits to, how that is enforced in code or CI, and where the limits are. Each claim points
to the file that backs it. If a claim here and the code disagree, the code is right and
this page has a bug: please [open an issue](https://github.com/diegoazh/agent-voice/issues).
If the mismatch has security impact (for example data leaves the machine, or something is
written or executed that this page says is not), do not open a public issue: report it
privately as described in [SECURITY.md](../SECURITY.md).

Scope today: **macOS only**, and **Claude Code is the only implemented runtime**. Codex,
OpenCode and Pi are planned (see [ROADMAP.md](../ROADMAP.md)). "Runtime" below means the
tool that produces the reply that is read aloud.

## 1. Local by design

Speech is synthesized on your machine with Kokoro through ONNX
([`src/agent_voice/engine.py`](../src/agent_voice/engine.py)). No hosted text-to-speech
service is involved, and the reply text is not sent anywhere by agent-voice.

The only network access in agent-voice's own code is `agent-voice download`
([`src/agent_voice/models.py`](../src/agent_voice/models.py)):

- URLs must be HTTPS, and a redirect to a non-HTTPS URL is refused.
- Each model file has a pinned SHA256 and size in the source. A download larger than its
  pinned size is aborted, and a hash mismatch discards the file.
- Data is written to a `.part` file and moved into place only after the hash matches.

Nothing else in `src/agent_voice/` opens a network connection. The limits of this claim
are in [section 7](#7-what-we-do-not-promise).

## 2. Your text is not stored

The reply text travels from the runtime's hook to a short-lived child process over a pipe
(stdin) and then stays in process memory ([`src/agent_voice/cli.py`](../src/agent_voice/cli.py),
`_spawn`; [`src/agent_voice/waiter.py`](../src/agent_voice/waiter.py)). agent-voice does
not write reply text to a file, a log or its config. The spawned child gets the text on
stdin, never in its command line.

What does touch disk is the synthesized **audio**, briefly
([`src/agent_voice/player.py`](../src/agent_voice/player.py), `speak`):

- Each chunk is written as a temporary WAV file with mode 0600 inside a directory created
  with `tempfile.mkdtemp` (mode 0700), and removed as soon as that chunk has been played.
- After a hard kill (`kill -9`) a file can remain. A later `speak` run removes leftover
  directories of agent-voice processes that no longer exist and are older than ten
  minutes.

Two commands read text that already exists elsewhere: `repeat` reads the runtime's own
transcript file (read-only), and `say-clipboard` reads the clipboard through `pbpaste`.

## 3. Minimal, reversible footprint

agent-voice writes in these places and no others:

| What | Where | Notes |
| --- | --- | --- |
| Settings (voice, speed, language, model, volume, enabled, known Claude config dirs) | `config.json` under `$AGENT_VOICE_HOME`, `$XDG_CONFIG_HOME/agent-voice` or `~/.config/agent-voice` | Atomic write (temp file, then replace). Never holds reply text. [`config.py`](../src/agent_voice/config.py) |
| Model files, `.verified` sidecars | `models/` under `$AGENT_VOICE_HOME`, `$XDG_DATA_HOME/agent-voice` or `~/.local/share/agent-voice` | Only written by `agent-voice download`. [`models.py`](../src/agent_voice/models.py) |
| Process state: pid files, lock files | `run/` under `$AGENT_VOICE_HOME`, else `$XDG_STATE_HOME/agent-voice` or `~/.local/state/agent-voice` | Directory created with mode 0700. Holds a pid and a process start time, never text. [`player.py`](../src/agent_voice/player.py), [`waiter.py`](../src/agent_voice/waiter.py) |
| Temporary WAV files | System temp directory | See [section 2](#2-your-text-is-not-stored). |
| Claude Code hook | `settings.json` in each Claude Code config directory, only after `agent-voice install claude` | See below. [`adapters/claude.py`](../src/agent_voice/adapters/claude.py) |
| On-screen display helper | Compiled binary in `osd/` next to `config.json` | Compiled locally with `swiftc` by `agent-voice osd build`. A successful `install claude` also attempts that build on macOS, unless `AGENT_VOICE_OSD` is set to `0`, `off`, `false` or `no` (case-insensitive) to opt out. It needs `swiftc`; if `swiftc` is missing or the build fails, install prints a one-line notice and still succeeds. An already-built helper is reused. Nothing else compiles it. [`osd.py`](../src/agent_voice/osd.py), [`cli.py`](../src/agent_voice/cli.py) |

What `install claude` does to Claude Code's `settings.json`:

- It adds or updates one entry under `hooks.Stop`: a command that runs
  `agent-voice hook claude`. Other settings and other hooks keep their values; the file
  is re-serialised as JSON with two-space indentation, so its formatting may change.
- Each file is replaced atomically. A symlinked `settings.json` is written through, not
  replaced.
- Before the first change to a file it keeps a one-time copy named
  `settings.json.agent-voice.bak` next to it.
- `agent-voice uninstall claude` removes only that entry. It also removes the `hooks` or
  `Stop` container if it is left empty and install had created it (judged from the
  `.bak`), re-serialises the file the same way, and drops that directory from
  `claude_config_dirs` in `config.json`. It does not remove the `.bak` file.

`agent-voice statusline` and `agent-voice keys skhd` print text and edit nothing.

## 4. Speaks only when you are there

Automatic speech is gated on focus, so a reply is not read aloud into an empty room
([`src/agent_voice/focus.py`](../src/agent_voice/focus.py),
[`src/agent_voice/adapters/claude.py`](../src/agent_voice/adapters/claude.py)):

- Today the gate supports Ghostty running under herdr. When the session's pane is not in
  front, the reply is held in memory by a waiter process and spoken when the pane gains
  focus. A newer reply for the same pane replaces the pending one.
- If focus cannot be determined when the reply arrives (at the hook), it is spoken rather
  than dropped. If there is no supported terminal setup, it is spoken immediately.
- A reply that is already waiting for focus is dropped without being spoken if its pane
  closes, if automatic speaking is turned off, if focus stays unknown for 10 minutes, or
  when `pending_max_wait_s` expires (default 0, no limit)
  ([`waiter.py`](../src/agent_voice/waiter.py)).
- Headless runtime sessions are skipped: the Claude Code hook returns without speaking
  when `CLAUDE_CODE_ENTRYPOINT` starts with `sdk-`. This depends on Claude Code setting
  that variable; agent-voice does not verify how the session was started.
- Explicit commands (`speak`, `repeat`, `say-clipboard`) are not focus-gated: you asked for them.

Automatic speaking is also off until you turn it on (`agent-voice on`;
`enabled` defaults to `false` in [`config.py`](../src/agent_voice/config.py)).

## 5. No shell, no injection

- agent-voice's code does not use `shell=True`, `os.system` or `os.popen`; every external
  command is started with a fixed argument list.
- Reply text is passed only on stdin, never as an argument
  ([`cli.py`](../src/agent_voice/cli.py), `_spawn`).
- The child process is started with a safe working directory and, on Python 3.11+, `-P`,
  so a file named `json.py` or an `agent_voice/` folder in the current project is not
  imported.
- herdr pane ids are validated against a strict pattern and cannot start with `-`
  ([`herdr.py`](../src/agent_voice/herdr.py), `valid_pane_id`).
- The hook command written into `settings.json` is quoted with `shlex.quote`, and
  uninstall recognises only the exact form `<path>/agent-voice hook claude`
  ([`adapters/claude.py`](../src/agent_voice/adapters/claude.py), `hook_command`,
  `_is_ours`).

## 6. A watched supply chain

These are automated gates, not an audit.

- One direct runtime dependency, pinned exactly: `kokoro-onnx==0.6.1`
  ([`pyproject.toml`](../pyproject.toml)). Its transitive dependencies are resolved in
  [`uv.lock`](../uv.lock), which records artifact hashes.
- CI scans (gitleaks, osv-scanner, trivy, semgrep, bandit) run on pushes to `main` and on
  pull requests, and local pre-commit hooks run gitleaks, semgrep and bandit. The
  scanners and their failure thresholds are listed once, in
  [SECURITY.md](../SECURITY.md#automated-scanning); the workflow files are the source of
  truth ([`.github/workflows/security.yml`](../.github/workflows/security.yml),
  [`trivy.yaml`](../trivy.yaml), [`.pre-commit-config.yaml`](../.pre-commit-config.yaml)).
- Third-party GitHub Actions are pinned to version tags, not to commit hashes.

## 7. What we do not promise

- **Native dependencies.** We make no claim about the network or telemetry behaviour of
  the compiled libraries that come with the dependencies (onnxruntime, espeak-ng). We
  have not audited them. The "no network" statement covers agent-voice's own code only.
- **Nothing on disk.** Reply text is not written, but audio is, briefly
  ([section 2](#2-your-text-is-not-stored)), and a hard kill can leave it behind for a
  while. Settings, model files and process state are on disk too
  ([section 3](#3-minimal-reversible-footprint)).
- **Model integrity.** Model files are verified against hashes pinned in this repository,
  not against signatures. On the speak path agent-voice does not re-hash the large files:
  it trusts a small `.verified` sidecar as long as the file's size and modification time
  still match ([`models.py`](../src/agent_voice/models.py), `_is_verified`).
- **A hostile machine.** agent-voice does not protect against a malicious local user, or
  against someone who tampers with the model directory or the sidecar files.
- **Transactions across config directories.** Each `settings.json` is replaced atomically,
  but when several Claude Code config directories are installed in one command, the
  writes are not transactional: if one fails, the files already written stay written, and
  the error reports which ones changed.
- **External commands.** `afplay`, `ps`, `lsappinfo`, `pbpaste` and `swiftc` are looked up
  on `PATH`, and `herdr` through `HERDR_BIN_PATH`, `PATH` or the fallback `/opt/homebrew/bin/herdr`. agent-voice does not verify
  those binaries.
- **Install from Git.** `uv tool install git+https://github.com/diegoazh/agent-voice` is
  not guaranteed to use the versions and hashes in `uv.lock`. We have not verified either
  way.
- **Scanners.** The automated gates in [section 6](#6-a-watched-supply-chain) catch known
  classes of problems. They are not a security audit, and they do not cover the compiled
  dependencies.
- **Other platforms and runtimes.** Linux, Windows and runtimes other than Claude Code
  are not implemented, so none of the above has been checked there.

## Third-party licences

agent-voice's own code is MIT. The components it depends on carry their own licences,
including GPL-3.0 components. See [Third-party licences](third-party-licences.md).
