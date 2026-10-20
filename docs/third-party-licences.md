# Third-party licences

This page lists the licence of agent-voice and of the main components it uses. It states
facts only and is not legal advice.

| Component | Licence | Source |
| --- | --- | --- |
| agent-voice | MIT | [`LICENSE`](../LICENSE) |
| kokoro-onnx | MIT | <https://github.com/thewh1teagle/kokoro-onnx> |
| Kokoro-82M model weights | Apache-2.0 | <https://huggingface.co/hexgrad/Kokoro-82M> |
| onnxruntime | MIT | <https://github.com/microsoft/onnxruntime> |
| phonemizer | GPL-3.0-or-later | <https://github.com/bootphon/phonemizer> |
| espeakng-loader | MIT (a wrapper that bundles the GPL-3.0 espeak-ng library) | <https://github.com/thewh1teagle/espeakng-loader> (the wrapper), <https://github.com/espeak-ng/espeak-ng> (the bundled library) |

## What this means in practice

- agent-voice's own code is MIT, and its package contains no GPL code: it builds only
  `src/agent_voice` ([`pyproject.toml`](../pyproject.toml)).
- Installing agent-voice pulls in GPL-3.0 components through its dependencies:
  phonemizer (GPL-3.0-or-later) and the espeak-ng library bundled inside espeakng-loader.
  The locked versions are in [`uv.lock`](../uv.lock).
- The Kokoro model files are not part of the package. `agent-voice download` fetches them
  at runtime from the kokoro-onnx GitHub releases (`model-files-v1.1`). The Kokoro-82M
  weights are Apache-2.0; the converted ONNX files in that release carry no separate
  stated licence.

## Credits

agent-voice builds on the work of the
[Kokoro](https://huggingface.co/hexgrad/Kokoro-82M) model authors,
[kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx), and
[espeak-ng](https://github.com/espeak-ng/espeak-ng).
