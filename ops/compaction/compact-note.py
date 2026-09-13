#!/usr/bin/env python3
"""compact-note.py — one prompt in, one continuity note out.

Prompt on stdin, note on stdout, diagnostics on stderr. Nothing else: no
tools, no session, no state on disk. Written 2026-09-04 to replace the the earlier agent runtime
Agent invocation the compactor had been using for this one call.

Why the replacement. the earlier agent runtime brought a 834 MB runtime and a 303 MB stateful
profile (a 100 MB state.db, a 64 KB skills prompt, `toolsets: [agent-cli]`,
`max_turns: 150`) to generate one piece of prose from a prompt that is already
self-contained -- the template opens "You are {{CHARACTER}}", so the agent
persona was inert at best. The script's own claim that the summarizer "gets no
tools" was true by prompt, not by construction. This file makes it true by
construction.

What it talks to: the OpenAI-compatible ``/v1/chat/completions`` contract --
surface S1 in the A3 serving-parity inventory, run-verified on BOTH candidate
open engines (llama.cpp llama-server and mlx_lm.server) as well as on the
LM Studio server in use today. Deliberately NOT the `lms` CLI, which is
surface S5 (model lifecycle, the workbench role) and the worst-parity surface
of the set. This call therefore survives an engine swap untouched; the model
bracket in the compactor does not, and that is the parked A3 decision's
business, not this file's.

Two behaviours worth stating, because both were previously inherited invisibly:

- **Sampling.** the earlier agent runtime' idiom is "None = provider default" and the profile
  declared no temperature, so every note so far was written at whatever the
  server defaults to for that model. That is preserved exactly: nothing is
  sent unless --temperature is passed. The difference is that it is now a
  stated choice rather than an invisible one.
- **Thinking.** the earlier agent runtime stripped ``<think>`` blocks from model output
  (agent/think_scrubber.py) -- an implicit dependency nobody had written
  down. Replaced by ``reasoning_effort: "none"`` on the request, the verified
  thinking kill for this stack, plus a defensive strip below for engines that
  emit the block regardless.

Privacy: the prompt carries companion content and arrives on **stdin**, never
argv -- this closes the `ps`-visibility caveat the compactor had accepted for
the the earlier agent runtime call. Diagnostics on stderr are counts and timings only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"  # last-resort loopback = the com.hearth.llm door (env, then serve.toml, win first)
THINK_RE = re.compile(r"\A\s*<think>.*?</think>\s*", re.S | re.I)


def endpoint(config: Path | None) -> tuple[str, str]:
    """(base_url, token), resolved env → serve.toml → loopback default.

    serve.toml holds a PATH to the token, never the token itself (the token-path
    idiom); this reads the file that path names and returns the value without
    ever printing it.
    """
    base = os.environ.get("LM_BASE_URL") or ""
    token = os.environ.get("LM_API_TOKEN") or ""
    if (not base or not token) and config and config.is_file():
        try:
            cfg = tomllib.loads(config.read_text(encoding="utf-8")).get("serve", {})
        except (OSError, tomllib.TOMLDecodeError) as exc:
            print(f"note: {config.name} unreadable ({type(exc).__name__})"
                  " — falling back to the loopback default", file=sys.stderr)
            cfg = {}
        base = base or str(cfg.get("lm_base_url") or "")
        src = str(cfg.get("lm_token_source") or "")
        if not token and src:
            path = Path(os.path.expanduser(src))
            if path.is_file():
                token = path.read_text(encoding="utf-8").strip()
    return (base or DEFAULT_BASE_URL), token


def generate(prompt: str, model: str, base: str, token: str, *,
             temperature: float | None, reasoning: str | None,
             max_tokens: int | None, timeout: float) -> tuple[str, dict, dict]:
    body: dict = {"model": model, "stream": False,
                  "messages": [{"role": "user", "content": prompt}]}
    if temperature is not None:
        body["temperature"] = temperature
    if reasoning:
        body["reasoning_effort"] = reasoning
    if max_tokens:
        body["max_tokens"] = max_tokens
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base.rstrip("/") + "/chat/completions",
                                 data=json.dumps(body).encode("utf-8"),
                                 headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("no choices in the response") from None
    # `timings` is llama-server's per-request telemetry (prompt/predicted token
    # counts + per-second rates) — telemetry LM Studio keeps to its own logs.
    # Absent elsewhere, it is simply {} and callers degrade gracefully.
    timings = data.get("timings")
    return ((msg.get("content") or ""), (data.get("usage") or {}),
            timings if isinstance(timings, dict) else {})


def main() -> int:
    ap = argparse.ArgumentParser(description="one prompt in, one note out")
    ap.add_argument("--model", required=True, help="model id/identifier to route to")
    ap.add_argument("--config", type=Path, help="serve.toml (endpoint + token path)")
    ap.add_argument("--temperature", type=float, default=None,
                    help="omitted by default — the server's default applies, "
                         "matching what every note so far was written at")
    ap.add_argument("--reasoning", default="none",
                    help="reasoning_effort sent with the request "
                         "('none' = the verified thinking kill; '' to send nothing)")
    ap.add_argument("--max-tokens", type=int, default=4096,
                    help="completion budget (the note asks for 600-900 tokens)")
    ap.add_argument("--timeout", type=float, default=1800.0,
                    help="seconds; a 122B single pass can run for minutes")
    ap.add_argument("--telemetry", type=Path, default=None,
                    help="write run telemetry (counts/rates/wall time + a one-line "
                         "summary — never content) as JSON to this path")
    args = ap.parse_args()

    prompt = sys.stdin.read()
    if not prompt.strip():
        print("empty prompt on stdin — nothing to summarize", file=sys.stderr)
        return 2

    base, token = endpoint(args.config)
    print(f"note: {len(prompt)} byte prompt → {base} as '{args.model}'"
          f"{' (no token)' if not token else ''}", file=sys.stderr)
    started = time.time()
    try:
        text, usage, timings = generate(prompt, args.model, base, token,
                                        temperature=args.temperature,
                                        reasoning=args.reasoning or None,
                                        max_tokens=args.max_tokens,
                                        timeout=args.timeout)
    except urllib.error.HTTPError as exc:
        print(f"LLM endpoint returned HTTP {exc.code}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"LLM endpoint unreachable ({type(exc).__name__}) at {base}",
              file=sys.stderr)
        return 1
    except (ValueError, RuntimeError) as exc:
        print(f"unusable response from the LLM endpoint ({exc})", file=sys.stderr)
        return 1

    text = THINK_RE.sub("", text).strip()
    if not text:
        print("the model returned an empty note", file=sys.stderr)
        return 1
    wall = time.time() - started
    print(f"note: {usage.get('completion_tokens', '?')} completion tokens "
          f"({usage.get('prompt_tokens', '?')} prompt) in {wall:.1f}s",
          file=sys.stderr)
    pps, gps = timings.get("prompt_per_second"), timings.get("predicted_per_second")
    if pps or gps:
        print(f"note: engine timings — prompt {timings.get('prompt_n', '?')} tok"
              f" @ {pps or 0:.0f} t/s, generated {timings.get('predicted_n', '?')} tok"
              f" @ {gps or 0:.1f} t/s", file=sys.stderr)
    if args.telemetry:
        parts = [f"note {len(text.split())}w in {wall:.0f}s"]
        if usage.get("prompt_tokens") is not None:
            parts.append(f"prompt {usage['prompt_tokens']} tok"
                         + (f" @ {pps:.0f} t/s" if pps else ""))
        if usage.get("completion_tokens") is not None:
            parts.append(f"gen {usage['completion_tokens']} tok"
                         + (f" @ {gps:.1f} t/s" if gps else ""))
        try:
            args.telemetry.write_text(json.dumps(
                {"summary": " · ".join(parts), "wall_s": round(wall, 1),
                 "model": args.model, "usage": usage, "timings": timings},
                indent=1), encoding="utf-8")
        except OSError as exc:
            print(f"note: telemetry not written ({type(exc).__name__})",
                  file=sys.stderr)
    sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
