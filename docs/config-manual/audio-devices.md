# Audio devices & sample rates

**Mic / speaker** → `LocalAudioTransportParams` (`bot ~L194`) — uses the macOS **default** in/out. Pin with `input_device_index=<n>` / `output_device_index=<n>`. Enumerate:
```bash
cd <the Hearth tree> && uv run python -c \
"import pyaudio;p=pyaudio.PyAudio();[print(i,p.get_device_info_by_index(i)['name']) for i in range(p.get_device_count())]"
```
**Sample rates** — `audio_in_sample_rate=16000` (`bot ~L197`); `audio_out_sample_rate=SAMPLE_RATE` (24000). ⚠️ **Do not change the input from 16000** — MLX-Whisper expects 16 kHz. Output must match the TTS engine's native rate.
