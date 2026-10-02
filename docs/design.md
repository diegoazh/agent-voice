# agent-voice — design and handoff

Local, privacy-first text-to-speech for coding agents. When an agent finishes
a turn, `agent-voice` speaks (part of) its reply aloud using Kokoro, fully on
the user's machine. One core CLI, thin adapters per agent.

This document is the starting point for the first implementation session. It
records what was researched (with sources), what was decided, and what is
still open.

## Goals

- Speak the agent's reply when it finishes a turn.
- 100% local: no text ever leaves the machine; nothing is persisted (no
  transcript copies, no debug logs with reply text).
- One installation shared by every agent and every account on the machine
  (e.g. personal `~/.claude` and work `~/.claude-work` Claude Code configs).
- Works with several agents: Claude Code, Codex CLI, OpenCode, Pi, Gemini CLI.
- Shareable as an open-source project later.

## Decisions taken (owner, 2026-10-02)

1. Name: `agent-voice` (project and CLI command). Easy to rename if needed.
2. Language for v1: **Python**, installed with `uv tool install`. Reason: the
   only stack with Spanish verified end to end (see "Engine"). A Rust port of
   the core can come later without touching the adapters, because adapters
   only pipe text into the CLI.
3. Engine: Kokoro via `kokoro-onnx`, local.
4. Architecture: core CLI + thin per-agent adapters (below).

## Open decisions (ask the owner, one at a time)

1. **What to read**: (a) only the opening of the reply — first paragraph, ~2–3
   sentences (recommended: replies lead with the answer, no extra tokens);
   (b) the full reply; (c) a purpose-written summary the agent appends to every
   reply (more precise, costs tokens and changes every reply).
2. Default voice: `ef_dora` (female) / `em_alex` / `em_santa` (male).
3. Model variant: fp32 (325 MB), fp16 (163 MB) or int8 (114 MB) — decide after
   measuring Spanish quality and latency.
4. Process model: one process per call vs. a small resident daemon that keeps
   the model loaded — decide after measuring load time.

## Engine (research, 2026-10-02)

`kokoro-onnx` (https://github.com/thewh1teagle/kokoro-onnx), v0.6.1 on PyPI
(2026-08-19), Python >=3.10,<3.14, MIT. Deps: `espeakng-loader`, `phonemizer`,
`numpy`, `onnxruntime` (https://pypi.org/pypi/kokoro-onnx/json).

- API: `Kokoro(model_path, voices_path)`;
  `create(text, voice, speed=1.0, lang="en-us", ...)` → `(samples float32,
  sample_rate)` (src/kokoro_onnx/__init__.py, examples/save.py).
- Spanish voices: `ef_dora`, `em_alex`, `em_santa`
  (https://huggingface.co/hexgrad/Kokoro-82M/raw/main/VOICES.md).
- `lang` is passed straight to espeak-ng via phonemizer. Spanish is most
  likely `"es"` (also try `"es-419"` for Latin American accent). **Unverified —
  test with a real sentence first.**
- **No system espeak-ng needed**: `espeakng-loader` bundles the library and
  data in the wheel (~9.9 MB for macOS arm64); the tokenizer only falls back to
  a system espeak if that fails (src/kokoro_onnx/tokenizer.py).
- Latency: README only says "near real-time on macOS M1". **Measure.**

Model files — release `model-files-v1.1` (pin this tag; `v1.0` has different
fp16/int8 sizes):
`https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/<file>`

| File | Size | SHA256 (GitHub asset digest) |
|---|---|---|
| `kokoro-v1.0.onnx` | 325.5 MB | `beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a` |
| `kokoro-v1.0.fp16.onnx` | 163.5 MB | `f3a290d384fbb27966d462905c71a46cef9e5fd00516b40df32a0b4afe77ac96` |
| `kokoro-v1.0.int8.onnx` | 114.1 MB | `ae315a79b623f244700e4afb9246c46a26066782e049ba174bf3ba433970ee9c` |
| `voices-v1.0.bin` | 28.2 MB | `bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d` |

The project publishes no separate checksum file; these are GitHub's per-asset
digests. Re-check with `shasum -a 256` on first download, pin them in code, and
refuse to load a file whose hash does not match.

Playback on macOS: write a temp WAV (stdlib `wave` + numpy float32→int16) and
play with `afplay` via `subprocess.Popen` (non-blocking); delete the temp file
afterwards. Keep the player PID to cut the previous utterance when a new one
arrives. (`sounddevice`/PortAudio only if streaming is needed.)

### Why not Rust for v1 (research, 2026-10-02)

- No Rust Kokoro implementation with Spanish was found. `lucasjinreal/Kokoros`
  (most known) supports English and partial zh/ja/de; others are small or
  undocumented (https://github.com/lucasjinreal/Kokoros, crates.io search).
- Spanish G2P would require FFI to espeak-ng (a C library + language data)
  packaged for macOS/Linux/Windows — the hardest part, not solved by any crate.
  `misaki-rs` is English-only (https://github.com/MicheleYin/misaki-rs).
- `ort` (ONNX Runtime for Rust) is still 2.0.0-rc.13 (https://ort.pyke.io/).
- Audio is fine in Rust (`rodio` 0.22.2).
- Model load time dominates in both languages; the mitigation (resident
  daemon) is the same.

## Agent integration (research, 2026-10-02)

| Agent | Mechanism | Reply text | Global config | Source | Status |
|---|---|---|---|---|---|
| Claude Code | `Stop` hook, command, JSON on stdin | `last_assistant_message` (docs recommend it over `transcript_path`, which can lag) | `~/.claude/settings.json` → `hooks.Stop[].hooks[]` `type:"command"`; `CLAUDE_CONFIG_DIR` selects another dir (read from the launch environment) | https://code.claude.com/docs/en/hooks, https://code.claude.com/docs/en/settings | Verified. Exit early if `stop_hook_active`; do not register on `SubagentStop`. |
| Codex CLI | `notify = ["cmd", ...]` in `~/.codex/config.toml` | JSON as the last argv (`sys.argv[1]`): `type:"agent-turn-complete"`, `last-assistant-message`, `cwd`, ... | `~/.codex/config.toml` | https://learn.chatgpt.com/docs/config-file/config-advanced | Verified (docs). |
| OpenCode | JS/TS plugin, `event` hook, `session.idle` | Event carries only `sessionID`; fetch `client.session.messages({path:{id}})`, take last `info.role==="assistant"`, join `parts` of `type:"text"` (skip `synthetic`) | `~/.config/opencode/plugins/` | https://opencode.ai/docs/plugins/, https://opencode.ai/docs/sdk/ | Mechanism verified; whether `session.idle` also fires for subagents is unknown. |
| Pi | TS extension, `pi.on("agent_end")` (or `agent_settled`) | `event.messages`: last `role:"assistant"`, join `content` of `type:"text"` (skip `thinking`, `toolCall`) | `~/.pi/agent/extensions/` | https://github.com/earendil-works/pi (docs/extensions.md, types.ts) | Types verified; `agent_end` can fire more than once (retries/compaction) — prefer `agent_settled` if it carries what we need. Whether `pi.exec` accepts stdin is unknown. |
| Gemini CLI | `AfterAgent` hook, JSON on stdin; must print only JSON (`{}`) to stdout | `prompt_response` | `~/.gemini/settings.json` → `hooks.AfterAgent[]` | https://github.com/google-gemini/gemini-cli (docs/hooks/reference.md) | Verified (docs). |

Adapters must return immediately (run the speech in the background) and must
never fail the agent's turn.

## Proposed architecture

- **Core CLI `agent-voice`**: reads text on stdin; options `--voice`,
  `--speed`, `--lang`; cleans the text; synthesizes with kokoro-onnx; plays it;
  cuts any previous utterance; never raises to the caller (errors go to stderr
  only, without reply text). Models in `~/.local/share/agent-voice/`, verified
  by hash. On/off switch (e.g. `agent-voice off|on|status`).
- **Optional daemon** (only if measurement demands it): keeps the model loaded,
  listens on a Unix socket; the CLI becomes a thin client.
- **Adapters** in `adapters/<agent>/`, each only extracting the reply text and
  piping it to the CLI, plus an `agent-voice install <agent> [--config-dir ...]`
  helper that registers the hook in the right config (supports several
  `CLAUDE_CONFIG_DIR`s).
- **Text cleaning before speaking**: drop fenced code blocks and inline code,
  URLs, file paths, long hashes, tables and separators, markdown markers
  (`#`, `*`, `_`, `>`, bullets; `[txt](url)` → `txt`), emojis; cap length (the
  model also errors past `MAX_PHONEME_LENGTH`); if the reply is mostly code,
  say nothing (or a very short fixed phrase).

## Privacy rules (non-negotiable)

- No network at runtime; only the one-time model download, hash-verified.
- Never write reply text to disk (no logs, no "last response" files). Temp WAV
  files are deleted right after playback.
- Reviewed and rejected as-is (2026-10-02): `rhishi99/OutLoud` (default engine
  `edge-tts` sends text to Microsoft; keeps `last-response.txt`) and
  `cris-m/claude_voice` (active `debug_hook.py` appends the full reply to
  `/tmp/claude_hook_debug.json`, never cleaned).

## First session — suggested order

1. Ask open decision 1 (what to read).
2. Spike: download the int8 + voices files (verify hashes), synthesize one
   Spanish sentence with `lang="es"` and each voice; measure model load and
   synthesis time on this Mac. Decide voice, variant and process model.
3. Scaffold the package with `uv` (pyproject, src layout, tests with pytest),
   TDD for the text-cleaning and hash-verification units.
4. Claude Code adapter first (both `~/.claude` and `~/.claude-work`), then
   Codex, OpenCode, Pi, Gemini.
