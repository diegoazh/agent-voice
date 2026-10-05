# ODD Feature: playback-control-extras

**Branch:** `feat/playback-controls` (based on `feat/core-cli`, local only).
**Created:** 2026-10-04. **Owner:** Diego. **TDD:** strict, enabled (source: global). **Runner:** `uv run pytest`.
**Coverage floor:** >=85% line AND >=85% branch of implemented/modified executable code (`--cov=agent_voice --cov-branch`).
**Delivery strategy:** `ask-on-risk` (ask once for chain strategy if authored changes exceed ~400 lines).
**RDD:** on (global). After each work-unit commit run `gentle-ai review assess ... --committed-only --json`; review per candidate as the native flow dictates.

## Objective

Add three playback/reading controls to agent-voice before feature 2 (Pi adapter):
pause-and-resume, repeat an arbitrary earlier reply by index, and speak the clipboard.

## Problem / Why

Diego wants to (1) pause speech and resume from exactly where it stopped, and
(2) read any previous message, not only the last one. Two complementary readers
were chosen: clipboard (any painted message, agent-agnostic) and transcript index
(clean source text from the agent's own transcript).

## Scope (authorized edit surfaces)

- `src/agent_voice/cli.py` — new subcommands `pause`, `say-clipboard`; `repeat` optional `[N]`; `keys` snippet additions.
- `src/agent_voice/player.py` — pause/resume signalling; `stop` SIGCONT-before-SIGTERM fix.
- `src/agent_voice/adapters/claude.py` — `nth` parameter on reply readers.
- `tests/` — `test_cli.py`, `test_player.py`, `test_claude_transcript.py`.
- `README.md` — document the three new commands and hotkeys (docs, with their task).

Out of scope: A2 (hotkey auto-copy via osascript) — deferred as a later optional enhancement.

## Constraints / settled design

- **Pause** is a single toggle command `pause`: `ps -o stat=` of the speaker pid; a stopped
  state (`T`) → SIGCONT, else → SIGSTOP. Signal the process GROUP (`os.killpg`) so the
  `afplay` child freezes too. Guard: only killpg when the speaker pid is its own group leader
  (`os.getpgid(pid) == pid`) — true for detached speakers (hook, `repeat --detach`); never
  signal a foreground speaker's group (would hit the user's shell). No-op when no verified speaker.
- **stop** must SIGCONT a paused speaker before/with SIGTERM, or a paused process never dies.
- **repeat N**: `repeat` (no arg) = last = `repeat 1`; `repeat 2`, `3`... skip N-1 qualifying
  assistant (non-sidechain, non-empty text) entries scanning backwards; out of range → the
  existing text-free error path (exit 1). Keep the strict no-fallback pane rule intact.
- **say-clipboard**: read `pbpaste` via a bounded `subprocess.run` (timeout); empty/failed
  clipboard → text-free stderr + exit 1 (mirror the `repeat` error shape). Speaks via
  `_speak_text(..., always=True)`, honoring `--detach/--voice/--speed/--lang/--model`.
- All generated artifacts in English. Speaker philosophy preserved: never store/print reply text.

## Tasks

- [x] **T1 — repeat with index (`repeat [N]`)** — DONE, commit `6b12b55`.
  - RED: `_reply_of(path, nth=N)` skips N-1 qualifying entries; `repeat 2` returns one-before-last;
    out-of-range → None → text-free error; `repeat 0`/negative rejected.
  - Code: `claude.py` `_reply_of(..., nth=1)` + wrappers (`last_reply`, `session_reply`,
    `project_reply`) + `resolve_reply(dirs, nth)` + `cli._cmd_repeat` positional `n` (nargs="?", type=int, default=1).
  - Tests: `test_claude_transcript.py` (nth), `test_cli.py` (parsing + `last` fixture signature).
  - Route: delegated writer. Trigger: writer (2+ non-trivial files).
  - Evidence: 12 RED failures first → full suite `uv run pytest` 529 passed. Coverage spot-check
    (parent): `claude.py`/`cli.py` BrPart=0; every changed line covered, no partial branches →
    line AND branch floor cleared on changed code. Deviation: `repeat 0`/`-1` rejected in
    `_cmd_repeat` (text-free `"...index must be 1 or greater"`, exit 2), not via argparse.

- [x] **T2 — pause/resume toggle (`pause`)** — DONE, commit `98cb795`.
  - RED: SIGSTOP when running, SIGCONT when stopped (injectable state fn); group-leader guard;
    no-op without verified speaker; `stop` SIGCONT-before-SIGTERM on a paused speaker.
  - Code: `player.py` `pause(...)` + `process_state` + `_signal` (reuse `_read_record`,
    `_is_previous_speaker`, `process_start_time`, `runtime_dir`); patched `_terminate_previous`
    (best-effort SIGCONT before SIGTERM). `cli._cmd_pause` + subparser + `ctrl+alt-p` hotkey line.
  - Tests: `test_player.py` (SIGSTOP/SIGCONT by state, group-leader guard, no-op, ProcessLookup/
    Permission/getpgid failures, stop-SIGCONT-before-SIGTERM, real sleeper toggle), `test_cli.py`
    (dispatch + keys exact-output).
  - Evidence: 19 RED → full suite 546 passed. Coverage spot-check: player.py 96% / cli.py 89%;
    the 3 player.py partial branches (247,303,305) are pre-existing (identical with changes stashed),
    changed code fully covered → floor cleared. Native review: high-risk, consent GRANTED,
    4-lens **0 findings, approved, acknowledged (authority burned)**.
  - Behavior note: if `ps` can't read state, `pause` defaults to treating the speaker as running (SIGSTOP).
  - Route: delegated writer. Trigger: writer (2+ non-trivial files).

- [x] **T3 — say-clipboard (`say-clipboard`)** — DONE, commit `5c8662c`.
  - RED: reads clipboard → speaks; empty/failed clipboard → text-free error exit 1; flag set honored.
  - Code: `cli._cmd_say_clipboard` + `say-clipboard` subparser (`--voice/--speed/--lang/--model/--detach`)
    + `_clipboard_text()` bounded `pbpaste` runner. Hotkey: `ctrl + alt - c : <exe> say-clipboard --detach`.
    Empty/`None`/whitespace clipboard → `"agent-voice: nothing in the clipboard to speak"` (stderr, exit 1).
  - Tests: `test_cli.py` (clipboard mocked, empty/None/whitespace error, flag forwarding, detach argv,
    `_clipboard_text` paths, a privacy test asserting clipboard text persists to no file), keys test updated.
  - Evidence: RED 6 failed + 7 errors → full suite 557 passed. Coverage: cli.py 90%, BrPart 0, changed
    lines covered → floor cleared. Native review: high-risk, consent GRANTED, 4-lens **0 findings,
    approved, acknowledged (authority burned)**.
  - Route: delegated writer. Trigger: writer (2+ non-trivial files).

## Acceptance criteria

- All three commands work via `agent-voice <cmd>`; hotkey snippet lists `p` and `c`.
- `repeat 2` speaks the one-before-last reply; out-of-range stays text-free.
- Pausing a detached speaker stops audio and resumes from the same point; a paused speaker
  can still be stopped by `stop`/`off`.
- No reply/clipboard text ever written to disk or stderr.
- Coverage floors met on changed code; `uv run pytest` green (integration tests may skip without models).

## Progress / evidence

- 2026-10-04: feature doc created, branch `feat/playback-controls` cut from `feat/core-cli` (HEAD 188f3a0). Mapping done (Explore agent).
- 2026-10-04: T1 done, commit `6b12b55`. Parent coverage spot-check confirmed BrPart=0 on changed modules.
- 2026-10-04: native review of the T1 slice (base-ref 188f3a0, high-risk/process_boundary) — consent GRANTED by Diego, 4-lens review (risk/resilience/readability/reliability), **0 findings, approved, acknowledged (authority burned)**. Reviewed boundary advances to the T1 slice tip.
- 2026-10-04: T2 done, commit `98cb795`. Native review of the T2 slice (base-ref 7868183, high-risk) — consent GRANTED, 4-lens **0 findings, approved, acknowledged (authority burned)**. Boundary advances to the T2 slice tip.
- 2026-10-04: T3 done, commit `5c8662c`. Native review of the T3 slice (base-ref 33ab227, high-risk) — consent GRANTED, 4-lens **0 findings, approved, acknowledged (authority burned)**.

## Status: FEATURE COMPLETE (local)

All three tasks implemented, TDD-verified, independently reviewed (0 findings each). Full suite `uv run pytest` 557 passed. Branch `feat/playback-controls` is LOCAL ONLY — push / PR / merge remain Diego's decisions (delivery strategy `ask-on-risk`).

## Next step

Delivery decision (push + PR). Deferred follow-up: A2 (hotkey auto-copy via osascript).
