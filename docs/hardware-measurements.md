# Hardware measurements

The numbers behind [Hardware requirements](HARDWARE-REQUIREMENTS.md): what each download of the
default model occupies in memory, how that was measured, and the rule Hearth applies to decide
whether a file fits on your Mac. Read the requirements page first; this one assumes its
three words (*quant*, *context*, *resident*).

## Where the numbers come from

Measured on 2026-09-08 on a Mac Studio M3 Ultra. The model server was started on each file of
[`unsloth/Qwen3.6-35B-A3B-MTP-GGUF`](https://huggingface.co/unsloth/Qwen3.6-35B-A3B-MTP-GGUF),
the set of files Hearth is built around, and its resident size was read once it reported
healthy. That model is a ~35B-parameter, 3B-active mixture-of-experts; this is the only
published set of its files that keeps the model's multi-token-prediction layer, which the
voice loop leans on. Weights were locked in memory (`--mlock`), KV cache at f16, one slot for
the 16k/32k/64k columns and the shipped shape (four slots sharing 262k) for the last.

Also tested, on 2026-09-07 with the same suite: nine quants of [a derivative build of the same model](https://huggingface.co/llmfan46/Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved-GGUF). All nine passed the same checks. It is the model the author talks to daily, and its Q8_0 matches the stock Q8_0 above in file size (37.8 GB), resident size (45.2 GB at full context) and speed (80.5 against 77.2 tokens/s). Those results stay in the project's records rather than on this page, which measures how well a model runs on your hardware.

## What each quant occupies (the model server alone)

| Quant (the file you download) | File on disk | Resident, 16k context | Resident, 64k | Resident, full context (262k) | Speed on the test machine |
|---|---|---|---|---|---|
| Q8_0 (the default) | 37.8 GB | 40.2 GB | 41.2 GB | 46.3 GB | 77 tokens/s |
| UD-Q6_K | 30.0 GB | 32.5 GB | 33.7 GB | 38.8 GB | 81 tokens/s |
| UD-Q5_K_M | 27.1 GB | 29.6 GB | 30.7 GB | 35.8 GB | 82 tokens/s |
| UD-Q5_K_S | 25.5 GB | 28.1 GB | 29.2 GB | 34.3 GB | 82 tokens/s |
| UD-Q4_K_M | 22.7 GB | 25.0 GB | 26.2 GB | 31.2 GB | 84 tokens/s |
| UD-Q4_K_S | 21.4 GB | 23.7 GB | 24.8 GB | 29.9 GB | 86 tokens/s |
| MXFP4_MOE | 22.2 GB | 24.7 GB | 25.9 GB | 30.9 GB | 78 tokens/s |
| UD-Q3_K_M | 17.1 GB | 19.4 GB | 20.6 GB | 25.7 GB | 88 tokens/s |

Sizes are decimal gigabytes, the unit macOS shows. The context costs about **0.37 GB per 16k
tokens** for every quant, so past a point the conversation length you allow moves the number
more than the file does. Speed is measured tokens per second while the model talks, with
speculative decoding on; it barely depends on the quant on this chip, because the model's
active part is small.

All eight files passed the same fitness checks: no leaked thinking, image input works, clean
endings, no repetition, and a planted fact found anywhere in a 64k context. Whether the smaller
quants **sound** different in a talk is still being judged; until then, treat Q8_0 as the
reference and anything from UD-Q4_K_M up as a reasonable trade.

## The fit rule

Beside the model, the speech models hold ~4.6 GB (TTS ~3 GB, STT ~1.6 GB) while a talk is up,
and macOS plus the Python runtime want ~8–12 GB more. Hearth's own fit check
(`hearth.weights scan`) budgets it this way: the graphics layer lets one program take roughly
**75 % of a Mac's memory** on machines up to 128 GB (more on larger ones); subtract the speech
reserve and a one-gigabyte margin, and what is left is what the model server may occupy. That is
stricter than "does the file fit in RAM", and it is what decides whether Hearth loads a file at
full context, caps the context, or refuses.

The verdict table on the requirements page applies that rule to the measured sizes. It has not
yet been confirmed by hand on a 32, 48 or 64 GB machine.

| Your Mac's memory | Which quants fit, by that rule |
|---|---|
| 32 GB | None. UD-Q3_K_M at a 16k context needs 19.4 GB against a budget of about 18.3 GB. |
| 48 GB | UD-Q4_K_S and UD-Q3_K_M at full context; UD-Q4_K_M, MXFP4_MOE and UD-Q5_K_S with the context capped at 64k; UD-Q5_K_M only at 32k. Q8_0 and UD-Q6_K do not fit. |
| 64 GB | Everything. Q8_0 with the context capped near 64k; UD-Q6_K and below at full context. |
| 96 GB and up | Everything at full context. |

The verdicts hold for the derivative build as well: its Q8_0 has the same footprint as the stock Q8_0.

## Cold starts and long contexts

Reading a fresh conversation into the model (a *cold* context) costs time that depends on its
length, not on the quant: about 4 s per 8k tokens, 20 s per 32k, 55 s per 64k on the test
machine. A conversation the server already holds continues in under a second. Hearth keeps the
talk in the server between turns, which is why long talks stay responsive.

## Smaller chips

Only the M3 Ultra has been measured. Expect the model's talking speed to scale roughly with
memory bandwidth: an M4 Max has about two thirds of the M3 Ultra's, an M4 Pro about a third.
That is an estimate, not a measurement. Memory sizes do not change with the chip.

## Lowering the floor

- **A smaller quant of the same model** — UD-Q4_K_M cuts the download from 37.8 GB to 22.7 GB
  (about 25 GB resident in a short talk) and UD-Q3_K_M to 17.1 GB. Every file in the table is a
  drop-in: point `model =` in `config/active.toml` at it.
- **A shorter context** — starting the server with a 64k context instead of the model's full
  262k saves about 5 GB, and is what lets Q8_0 fit on a 64 GB machine. Hearth's fit check does
  this capping for you.
- **A lighter model** — a smaller mixture-of-experts (e.g. a ~24B, 2B-active model) has a lower
  footprint and swaps in via `model =` in `config/active.toml`.
- **Lower-quant TTS** — `chatterbox-turbo-{8bit,6bit,4bit}` saves ~1–2 GB at some
  voice-quality cost.
