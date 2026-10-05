# Roadmap

## Done / available now

- Core CLI: `on`, `off`, `toggle`, `status`, `voice`, `model`, `download`, `speak`.
- Claude Code adapter: end-of-turn hook, `repeat`, `install claude` / `uninstall claude`, and focus-gated auto-speak.
- Playback controls: `pause`, `stop`, `repeat N`, `say-clipboard`.
- Configurable reading speed: `speed up|down`.
- Reading language switch: `lang es|en`.
- Hotkeys for skhd: `keys skhd`.
- Automated security scanning (pre-commit + CI).
- MIT license.

## Planned (in order, not yet implemented)

1. Pi adapter (next).
2. Codex adapter.
3. OpenCode adapter.
4. Gemini CLI adapter.

## Ideas / contributions welcome

- Linux and Windows support:
  - a portable audio backend to replace macOS `afplay`;
  - a portable focus strategy to replace Ghostty / `herdr`.
