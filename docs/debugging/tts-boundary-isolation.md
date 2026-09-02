# Isolating the TTS boundary now that it's in-process

The TTS boundary is an in-process Python object — probe it with the **standalone unit test**, which exercises `run_tts` with no mic/LLM/pipeline:

```bash
cd <the tree> && LM_API_TOKEN=x $UV run python tests/test_mlx_tts.py   # writes tests/step1_unit_out.wav; prints TTFA/RTF/frames
afplay tests/step1_unit_out.wav
```
Frames stream + it sounds like clean speech → the TTS boundary is healthy; the bug is elsewhere (STT/LLM/transport — use the general Plays). Import/load crash here → it's the engine/env (transformers pin, raw-weights, or the stream case study).
