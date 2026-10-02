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
| T3 | Text cleaning: markdown -> prose, placeholders, simple tables as lists, sentence chunking | delegated | done `ef3b4a0`, `dc2df71` |
| T4 | Model store: paths, SHA256-pinned download, variant selection | delegated | done `1ca6a6b` |
| T5 | Synthesis engine with espeak short-path workaround | delegated | done `13dd059` |
| T6 | Pipelined playback with afplay, cut previous utterance, no text on disk | delegated | done `2cfd65e`, `21a95b8` |
| T7 | CLI: stdin speak, on/off/status, voice/model persistent config, flags | delegated | done `a58304d`, `981c17b`, `16018da` |
| T8 | Claude Code adapter + `agent-voice install claude` (~/.claude, ~/.claude-work); verify Stop-hook text excludes intermediate text | delegated | done `0508485`, `a0b5465` |
| T9 | `agent-voice repeat` with Claude Code transcript reader | delegated | done `4f56390` |
| T10 | Adapters + repeat readers for Codex, Pi, OpenCode, Gemini | later | pending |
| T11 | Hardening of review follow-ups (owner approved 2026-10-02) | delegated | done except item 4 (moved to T12) |
| T12 | Focus gating (owner 2026-10-02): speak only when the session has focus (Ghostty frontmost + herdr pane focused); otherwise keep the reply pending in memory and speak it when that pane gains focus; plus T11 item 4 fix | delegated | in progress |

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

- T3 `ef3b4a0` feat(text): 69 tests pass; text.py ~99.5% line / 97.4%
  branch; RED observed per behavior group. Independent verifier: PASS on all
  criteria, black-box probe included. Only Python 3.12 exercised. RDD:
  assessed medium; owner declined review for this candidate.
  Open product question raised by the probe: inline code inside prose
  produces repetitive "ver el código en el texto" (e.g. "usaba ver el código
  en el texto en vez de ver el código en el texto"); paths written in
  backticks get the code placeholder, not the link one. Resolved below.
- Review of the feature document (da6e02a content): owner granted; native
  review approved (1 lens, 2 informational suggestions), acknowledged,
  authority burned.
- T3b `dc2df71` feat(text): owner-agreed inline rule (short identifier ->
  spoken word; path/URL in backticks -> link placeholder; real code ->
  code placeholder once per sentence). RED 20 failed / GREEN 99 passed;
  text.py 99%. Dots in identifiers are spoken "punto" (`cli.py` -> "cli
  punto py"); a dropped second code span can leave a dangling connector
  ("y después."). Independent verifier: PASS.
- T4 `1ca6a6b` feat(models): pinned store, sidecar-verified resolve (no
  hashing or network at speak time), verified atomic download, adopts
  existing files. 26 tests, models.py 100%/100%. TDD deviation (honest):
  the whole test file was written before the module, so RED was a single
  collection ImportError, not one RED per behavior. Independent verifier:
  PASS (black-box probe incl. stale/corrupt sidecar, checksum mismatch,
  http refused). Real fp32+voices sidecars written to the models dir.
- RDD: range da6e02a..1ca6a6b assessed medium (627 lines); owner declined
  review for this candidate.

- T5 `13dd059` feat(engine): Engine over kokoro_onnx (em_alex, es-419,
  speed 0.5-2.0), in-memory WAV, espeak workaround. Finding: espeak-ng reads
  its relative data path on every phonemization, so cwd is switched around
  Kokoro construction and each create() call (process-wide, restored in
  finally); applied only when the data path exceeds 100 chars. Integration
  test with the real fp32 model passes (~2.3 s incl. load; later calls
  ~0.5 s). engine.py 100%/100%. One RED per behavior observed; 3 behaviors
  passed first run (1 mutation-proven). Independent verifier: PASS.
- T6 `2cfd65e` + `21a95b8` feat/fix(player): pipelined playback (bounded
  queue), private 0700 temp dir, WAV deleted after each play, flock-guarded
  pid file holding PID + process start time; previous speaker SIGTERMed
  only if alive and start time matches (PID-reuse safety fix requested by
  orchestrator). player.py 94.4% line / 91.7% branch; 3 stable runs.
  Independent verifier: PASS incl. real two-process cut (0.28 s), unrelated
  process never signalled, marker text never on disk. Note: `play` errors
  propagate; T7 CLI must catch them.
- RDD: range d459cb1..21a95b8 assessed HIGH (process spawning); owner
  declined review for this candidate; RDD-off high tier satisfied by writer
  self-verification + independent verifier.
- T7 `a58304d` feat(config) + `981c17b` feat(cli) + `16018da` fix(cli):
  JSON config (AGENT_VOICE_HOME > XDG_CONFIG_HOME > ~/.config), defaults
  disabled/em_alex/es-419/fp32/1.0; speak (+ --detach via new-session
  child, text over pipe), on/off/status, voice, model, download, stop;
  speak swallows every error with a text-free message and exits 0.
  cli.py/config.py 100%/100%. Independent verifier FAILED `--detach`
  (binary pipe received str -> TypeError swallowed -> never spoke; fakes
  hid it); real audio, cut-previous and `off` passed. RDD: assessed HIGH;
  owner GRANTED review; 4 lenses confirmed the same CRITICAL defect; one
  bounded correction `16018da` (27 lines: UTF-8 bytes + PYTHONIOENCODING,
  strict fake, real-pipe test, RED observed); targeted validation
  approved; acknowledged, authority burned.
  Follow-ups (advisory, non-blocking): broad except in status model check
  (cli.py:49-53, WARNING); stdin not drained when disabled with --detach
  (cli.py:141-143, WARNING); config values unvalidated; config
  read-modify-write race; voice validation split; a config test not
  asserting the raise.
- T8 `0508485` feat(claude) + `a0b5465` feat(cli): `agent-voice hook
  claude` (ignores stop_hook_active, uses last_assistant_message, detached,
  always exit 0, silent) and `install|uninstall claude [--config-dir ...]`
  (default $CLAUDE_CONFIG_DIR or ~/.claude; preserves other hooks; backup
  once; all dirs validated before writing). Docs
  (https://code.claude.com/docs/en/hooks): `last_assistant_message` is the
  final assistant text only, not intermediate text between tool calls
  (resolves the open check). 100%/100% on claude.py and cli.py.
  Independent verifier: PASS incl. real audio via the hook (return 0.08-0.11
  s direct), stop_hook_active ignored, no text on disk. RDD: HIGH; owner
  GRANTED; 4 lenses approved with no blocking findings; acknowledged.
  Follow-ups (WARNING, advisory): unquoted hook command path; os.replace
  would replace a symlinked settings.json with a regular file (owner's
  files are regular files today, checked); partial multi-dir write on
  OSError; uninstall may drop a foreign empty Stop list; settings error
  without file location.
- T9 `4f56390` feat(repeat): `last_reply` reads Claude Code's own JSONL
  (`<cfg>/projects/<cwd with non-alnum -> '-'>/<session>.jsonl`; subagents
  live in a separate `subagents/` dir; one content block per assistant
  entry), newest session preferring the cwd project, backward 64 KB block
  scan, final text-bearing non-sidechain assistant entry; never writes.
  `agent-voice repeat` works regardless of on/off (hidden `speak --always`
  for the detached child). 100%/100% claude.py/cli.py. Independent
  verifier: PASS incl. real read (1344 chars, 2.3 ms, matches an
  independent scan) and real audio while disabled. RDD: HIGH; owner
  GRANTED; 4 lenses approved; acknowledged.
  Follow-ups (WARNING): cli.py:161; backward reader quadratic on very long
  lines (claude.py:50-56). Gap: repeat only searches $CLAUDE_CONFIG_DIR or
  ~/.claude by default, not ~/.claude-work.
- Owner request (2026-10-02): run one general review of the whole
  `feat/core-cli` branch (from 20c93bb) after T9.

## Backlog (owner, 2026-10-02, later)

- Read selected or copied text aloud (selection/clipboard), as an additional
  source besides the agent transcript.

## Next step

Hardening of review follow-ups, then the general branch review.
