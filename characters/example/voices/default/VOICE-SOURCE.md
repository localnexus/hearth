# Voice source — the shipped default reference clip

`sample.wav` — the default voice's reference clip, cut from the **LJ Speech Dataset**
(Keith Ito, https://keithito.com/LJ-Speech-Dataset/), which is **public domain**
(LibriVox recordings of public-domain texts; the dataset is released into the public
domain by its author).

| Field | Value |
|---|---|
| Source clip | `LJ020-0002` (LJSpeech-1.1) |
| Passage | Cookery-book prose (one complete sentence, neutral vocabulary) |
| Duration | 10.10 s |
| Processing | Resample only: 22.05 kHz → 24 kHz, mono, Int16. No trimming, EQ, or normalization. |
| Selected | 2026-08-26, by ear-check from six screened candidates |

Transcript:

> Bread raised with what is known to bakers as a "sponge," requires more time and a
> trifle more work than the simpler form for which I have just already given directions.

This clip exists so the project ships with a **rights-clean, working default voice** out
of the box. It is an ordinary female English voice — pleasant and clear, not a character.
To give your companion a voice of its own, see `docs/bring-your-own-voice.md`.
