# agent-voice core — feature document

Revision: 2026-10-02 (created)

## Objective

Ship the `agent-voice` core CLI and the Claude Code adapter: when Claude Code
finishes a turn, speak the full reply aloud locally with Kokoro, skipping code,
URLs, paths and complex tables, and allow re-speaking the last reply.

## Problem and why

The owner wants to hear agent replies while away from the screen, fully local
and private. Existing tools leak text (edge-tts) or persist replies to disk
(see `docs/design.md`, "Privacy rules").

## Agreed decisions (owner, 2026-10-02)

- Read the **full reply**, never reasoning/thinking blocks.
- Markdown is read as plain prose (markers stripped; `[txt](url)` -> `txt`).
- Code (fenced and inline) is never spoken: placeholder "ver el código en el texto".
- URLs and file paths: placeholder "ver el link en el texto".
- Plain-text tables are read like a list, row by row; complex tables
  (any cell with code/URL/path, or more than ~4 columns / ~8 rows) get the
  placeholder "ver la tabla en el texto".
- Default voice `em_alex`, lang `es-419`; `ef_dora`, `em_santa` selectable
  persistently (`agent-voice voice <name>`) and per call (`--voice`).
- Default model fp32 (`kokoro-v1.0.onnx`); int8 and fp16 selectable by command.
- No resident daemon: sentence chunking with pipelined playback (play sentence
  N while synthesizing N+1). A new reply cuts the previous one.
- Automatic speaking once enabled (`agent-voice on|off|status`).
- `agent-voice repeat` re-speaks the last reply by reading the agent's own
  stored transcript (Claude Code first; Codex, Pi, OpenCode, Gemini later).
  agent-voice itself never persists reply text.

## Constraints

- Privacy rules in `docs/design.md` are non-negotiable: no network at runtime
  except the hash-verified model download; never write reply text to disk;
  temp WAVs deleted after playback; errors to stderr without reply text.
- Adapters return immediately and never fail the agent's turn.
- Python >=3.10,<3.14, `uv`, src layout. Generated artifacts in English;
  spoken placeholders are Spanish strings (product copy agreed above).
- Spike findings to honor: bundled espeak-ng fails with long data paths
  (needs short-path workaround); RTF fp32 ~0.38 on M1 Max.

## Verification mode

- TDD: **strict, enabled** (source: owner global instructions "Strict TDD
  Mode: enabled"). RED -> GREEN -> REFACTOR observed per behavior.
- Runner: `uv run pytest` (scaffolded in T2); coverage floor >=85% line AND
  >=85% branch on changed code (`pytest-cov`, `--cov-branch`).
- RDD: on (global). Assess each work-unit commit per ODD.

## Delivery

- Branch `feat/core-cli`, local only (repository has no remote). Work-unit
  commits per task, Conventional Commits, no AI attribution.
- Forecast: ~1500-2000 authored lines; strategy `ask-on-risk`. PR slicing is
  N/A until a remote exists.

## Tasks

| ID | Task | Route | Status |
|---|---|---|---|
| T1 | Update `docs/design.md` with today's decisions and spike findings | delegated (docs) | done `1bcea8b` |
| T2 | Package scaffold with uv (src layout, pytest, pytest-cov, CLI entry point) | delegated | done `eaeac8e` |
| T3 | Text cleaning: markdown -> prose, placeholders, simple tables as lists, sentence chunking | delegated | pending |
| T4 | Model store: paths, SHA256-pinned download, variant selection | delegated | pending |
| T5 | Synthesis engine with espeak short-path workaround | delegated | pending |
| T6 | Pipelined playback with afplay, cut previous utterance, no text on disk | delegated | pending |
| T7 | CLI: stdin speak, on/off/status, voice/model persistent config, flags | delegated | pending |
| T8 | Claude Code adapter + `agent-voice install claude` (~/.claude, ~/.claude-work); verify Stop-hook text excludes intermediate text | delegated | pending |
| T9 | `agent-voice repeat` with Claude Code transcript reader | delegated | pending |
| T10 | Adapters + repeat readers for Codex, Pi, OpenCode, Gemini | later | pending |

### Acceptance criteria (summary)

- T3: given markdown with code fences, inline code, URLs, paths, simple and
  complex tables, emojis, the output is speakable prose with exactly the agreed
  placeholders; long text splits into sentence chunks under the phoneme limit.
- T4: a file whose hash mismatches is refused and deleted; no network when files
  are present.
- T6/T7: speaking never blocks the caller; a second call stops the first; no
  reply text is written anywhere.
- T8: Claude Code turn end speaks the reply; `stop_hook_active` exits early; no
  `SubagentStop` registration.
- T9: `repeat` re-speaks the last assistant reply of the latest Claude Code
  session without agent-voice persisting text.

## Progress and evidence

- T1 `1bcea8b` docs: design.md updated (68+/38-). Structural readback by
  orchestrator: placeholders, repeat, spike results present. TDD/coverage N/A
  (documentation only).
- T2 `eaeac8e` build: uv scaffold. Worker evidence: RED
  `ModuleNotFoundError: No module named 'agent_voice.cli'`; GREEN 2 passed,
  100% line / 100% branch; REFACTOR no-op. Independent verifier (fresh
  context): PASS on all criteria (`uv lock --check` ok, 2 passed, 100%/100%,
  `agent-voice --version` -> `agent-voice 0.1.0`, no AI attribution).
  Only Python 3.12 exercised. T1 and T2 VERIFIED.
- RDD: range 20c93bb..HEAD assessed medium (`slice_budget_reached`, 908
  lines, mostly `uv.lock`); owner declined review for this candidate
  (`declined_this_candidate`). Reviewed boundary advances to HEAD under
  ordinary policy.

## Backlog (owner, 2026-10-02, later)

- Read selected or copied text aloud (selection/clipboard), as an additional
  source besides the agent transcript.

## Next step

T3 text cleaning.
