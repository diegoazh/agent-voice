# agent-voice

Local, privacy-first text-to-speech for coding agents, powered by [Kokoro](https://github.com/thewh1teagle/kokoro-onnx). Nothing leaves your machine.

**Supported agent today: Claude Code.** Codex, OpenCode, Pi, and Gemini CLI are planned but not implemented yet; see [ROADMAP.md](ROADMAP.md).

## Requirements / OS support

- **macOS: supported and tested.**
- **Linux / Windows: not supported yet.** Audio playback uses macOS `afplay`, and focus gating (speak only when the agent's terminal is focused) relies on Ghostty and `herdr`, both macOS-specific here. The synthesis core (Kokoro / onnxruntime) is cross-platform, so supporting other systems needs a portable audio backend and a focus strategy. Contributions welcome.
- Python >= 3.10 and < 3.14, and [`uv`](https://docs.astral.sh/uv/).

## Install

From a local checkout:

```sh
uv tool install --force .
```

Or straight from GitHub (placeholder URL until the repository is published):

```sh
uv tool install git+https://github.com/diegoazh/agent-voice
```

Then fetch the Kokoro model files (one-time download):

```sh
agent-voice download
```

## Usage

Run `agent-voice --help` for the full list.

**Control**

- `on` / `off` - enable or disable automatic speaking (`off` also stops playback).
- `toggle` - flip automatic speaking.
- `status` - show settings and whether model files are present.
- `pause` - pause or resume the current utterance.
- `stop` - stop the current utterance.

**Voice and speed**

- `voice [NAME]` - show or set the default voice.
- `lang [es|en]` - show or switch the reading language.
- `speed [up|down]` - show the speed, or step it up/down by 0.25.
- `model [NAME]` - show or set the model variant.

**Speaking**

- `repeat [N]` - re-speak a reply from the agent's transcript (default: the last).
- `say-clipboard` - speak the current clipboard contents.
- `speak` - read text from stdin and speak it.
- `pending-wait [DURATION|off]` - show or set how long a reply may wait for focus.

**Setup**

- `download` - download model files (the only network path).
- `install claude` / `uninstall claude` - register or remove the Claude Code hook.
- `keys skhd` - print a hotkey snippet (see below).

## Hotkeys (skhd)

`agent-voice keys skhd` prints a snippet to paste into `~/.config/skhd/skhdrc`. The tool never edits that file. Defaults:

| Hotkey | Action |
| --- | --- |
| `ctrl+alt+q` | stop |
| `ctrl+alt+r` | repeat last reply |
| `ctrl+alt+v` | toggle |
| `ctrl+alt+p` | pause / resume |
| `ctrl+alt+c` | speak clipboard |
| `ctrl+alt+right` | speed up |
| `ctrl+alt+left` | speed down |

## Privacy

Everything runs locally. The only network access is `agent-voice download`, which fetches the model files.

## Development

```sh
uv sync          # create .venv and install runtime + dev dependencies
uv run pytest    # run the tests
```

Design notes: [docs/design.md](docs/design.md).

## Security

See [SECURITY.md](SECURITY.md) for how to report a vulnerability.

## License

[MIT](LICENSE) (c) 2026 Diego A. Zapata Häntsch
