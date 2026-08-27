# STT — model, language, hallucination guard

> The STT service (`MLXWhisperSTTService`, incl. `run_stt` + `MLX_WHISPER_MODEL`) lives in **`stt_service.py`** — all the anchors below are there.

**Model** → `MLX_WHISPER_MODEL` (`stt_service ~L32`, default `mlx-community/whisper-large-v3-turbo`). Any MLX Whisper repo id; auto-downloads on first use.
**Language** → auto-detected. To pin: add `language="en"` to the `transcribe(...)` call (`stt_service ~L146`) and set it on the `TranscriptionFrame` (`~L179`). Pinning speeds detection + avoids wrong-language drift.
**Hallucination guard** → `run_stt` guard block (`stt_service ~L162–L175`): the `no_speech_prob > 0.6` threshold (raise toward 0.8 if real speech is dropped) and the phantom-phrase set (add words Whisper invents on silence in your room, or trim if it eats real short utterances).
