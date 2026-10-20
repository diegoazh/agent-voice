# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

Versioning rule (SemVer, pre-1.0): every PR that changes behavior bumps the
version in `pyproject.toml` and adds an entry here. `fix` -> patch, `feat` -> minor.

## [0.2.0] - 2026-10-20

### Added

- Per-block language detection (Spanish or English): each block of a reply is read with a matching voice, and text normalization is language-aware (#1).
- Per-sentence language detection inside paragraphs, so mixed-language paragraphs use the right voice for each sentence (#2).
- On-screen display of the new speed or volume after `speed up|down` and `volume up|down`, built explicitly with `agent-voice osd build` (#3).
- `agent-voice statusline` command that prints a one-line segment for Claude Code status-line scripts (#3).
- Playback volume control (`volume up|down`).
- Short inline code and paths are now read aloud.

### Fixed

- Fenced code blocks inherit the surrounding language, and a block is closed only by a fence at least as long as its opening one (#1, #2).
- The reply length cap is applied once per reply (#1).
- Inline code is ignored when detecting language, and common English words that do not collide with Spanish are recognized (#2).
- `#N` references are read as "numero N" / "number N" instead of spelling out the hash sign (#4).
- The Claude Stop hook stays silent in headless sessions (`claude -p` / SDK) (#5).
- Leftover bold and strikethrough markers are stripped from spoken text.
- Only one on-screen display is shown at a time (#3).

### Changed

- The version now lives only in `pyproject.toml`; `agent_voice.__version__` is read from the installed package metadata.

## [0.1.0] - 2026-10-05

- Initial public release.
