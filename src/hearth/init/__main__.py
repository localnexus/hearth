"""python -m hearth.init — the first-run bootstrap, spoken.

    .venv/bin/python -m hearth.init                 # ask what needs asking
    .venv/bin/python -m hearth.init --yes           # take every default, ask nothing
    .venv/bin/python -m hearth.init --memory on --lm-url http://127.0.0.1:1234/v1

Two questions can be asked, both skipped by --yes or when stdin is not a
terminal: whether the companion should remember across sittings (default no — memory
writes durable records about a person, and is never turned on silently), and
which model id to use when the LLM server advertises several. Everything
else is decided by the flags or their defaults, and every step is reported.
"""

from __future__ import annotations

import argparse
import os
import sys

from hearth.config import config_loader as cl

from . import (DEFAULT_LM_URL, PLACEHOLDER_ID, InitError, Report, copy_templates,
               current_model_id, enable_memory, facade_url, mint_token, open_gates,
               probe_models, set_lm_url, set_model_id)

_MARK = {"created": "+", "set": "+", "exists": "·", "unchanged": "·", "note": "!",
         "skipped": "-"}


def _say(state: str, text: str) -> None:
    print(f"  {_MARK.get(state, ' ')} {text}")


def _ask_yes_no(prompt: str, interactive: bool, default: bool = False) -> bool:
    if not interactive:
        return default
    hint = "[y/N]" if not default else "[Y/n]"
    ans = input(f"{prompt} {hint} ").strip().lower()
    return default if not ans else ans in ("y", "yes")


def _pick(ids: list[str], interactive: bool) -> str | None:
    """One id → take it. Several → ask, or leave the placeholder when nobody can
    answer (the closing message says so)."""
    if len(ids) == 1:
        return ids[0]
    if not interactive:
        return None
    print("  your model server lists several models:")
    for i, m in enumerate(ids, 1):
        print(f"    {i}. {m}")
    ans = input("  which one should the companion use? [number, or Enter to decide later] ").strip()
    if ans.isdigit() and 1 <= int(ans) <= len(ids):
        return ids[int(ans) - 1]
    return None


def want_serve(serve: bool, no_serve: bool, interactive: bool, ask) -> bool:
    """Whether init ends by BECOMING the facade (D-f, signed 2026-09-05).

    The flags decide when given; otherwise a person at a terminal is asked
    (default yes — one command, one URL), and nobody there means no: an
    unattended run must never turn into a process that does not return."""
    if serve:
        return True
    if no_serve or not interactive:
        return False
    return bool(ask())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hearth.init",
                                 description="first-run setup: templates, access key, switches")
    ap.add_argument("--yes", "-y", action="store_true",
                    help="take every default and ask nothing (memory stays off)")
    ap.add_argument("--memory", choices=("on", "off"),
                    help="enable cross-sitting memory without being asked (default: ask; off)")
    ap.add_argument("--lm-url", default=DEFAULT_LM_URL,
                    help=f"your OpenAI-compatible model server (default {DEFAULT_LM_URL})")
    ap.add_argument("--lm-token", default="",
                    help="access key for that server, only if it wants one (never stored)")
    ap.add_argument("--model-id", help="model id to record, skipping the probe")
    ap.add_argument("--no-probe", action="store_true", help="do not contact the model server")
    ap.add_argument("--serve", action="store_true",
                    help="when done, start Hearth in this terminal without asking")
    ap.add_argument("--no-serve", action="store_true",
                    help="when done, only print what to run next (never ask)")
    args = ap.parse_args(argv)
    interactive = sys.stdin.isatty() and not args.yes

    rep = Report()
    print(f"Hearth first run — setting up in {cl.DATA_DIR}")
    try:
        paths = copy_templates(rep)
        mint_token(paths["serve"], rep)
        open_gates(paths["serve"], rep)
        if args.lm_url != DEFAULT_LM_URL:
            set_lm_url(paths["serve"], args.lm_url, rep)

        want_memory = (args.memory == "on") if args.memory else _ask_yes_no(
            "Should the companion remember you between conversations? This writes records "
            f"under {cl.DATA_DIR / 'characters'}/<companion>/memory/ — on this machine only, "
            "readable by your account alone, deletable.",
            interactive)
        if want_memory:
            enable_memory(rep)
        else:
            rep.add("skipped", "memory stays off (python -m hearth.init --memory on later, "
                               "or the settings page)")

        model_path = paths["model"]
        chosen = args.model_id
        if chosen is None and not args.no_probe:
            ids = probe_models(args.lm_url, args.lm_token)
            if ids is None:
                rep.add("note", f"no model server answering at {args.lm_url} — the model "
                                "is left for later")
            elif not ids:
                rep.add("note", f"{args.lm_url} answers but lists no model yet")
            else:
                chosen = _pick(ids, interactive)
                if chosen is None:
                    rep.add("note", f"{len(ids)} models listed — pick one on the launch "
                                    "page, or re-run with --model-id")
        if chosen:
            set_model_id(model_path, chosen, rep)
    except InitError as exc:
        for state, text in rep.lines:
            _say(state, text)
        print(f"  x {exc}", file=sys.stderr)
        return 1

    for state, text in rep.lines:
        _say(state, text)
    if rep.token:
        print()
        print("  your access key (shown once — it lives in the file named above):")
        print(f"    {rep.token}")
    placeholder = current_model_id(model_path) == PLACEHOLDER_ID
    url = facade_url(paths["serve"])
    if placeholder:
        print()
        print(f"  ! no model chosen yet — the id is still \"{PLACEHOLDER_ID}\". The launch page")
        print("    offers the first-run walk, which lists what your model server has (or edit "
              f"{model_path.relative_to(cl.DATA_DIR) if model_path.is_relative_to(cl.DATA_DIR) else model_path}).")
    print()
    serve_now = want_serve(args.serve, args.no_serve, interactive, lambda: _ask_yes_no(
        "Start Hearth now, in this terminal? (Ctrl-C stops it; the mic prompt lands here.)",
        True, default=True))
    if serve_now:
        print(f"Starting Hearth. Open   {url}")
        print("  and paste the key when the page asks — once; that browser keeps it. From there,")
        print("  Start brings the companion up.")
        print(flush=True)
        os.execv(sys.executable, [sys.executable, "-m", "hearth.serve"])
    print("Next:")
    print("  1. start Hearth:       .venv/bin/python -m hearth.serve")
    print(f"  2. open                {url}")
    print("     and paste the key when the page asks — once; that browser keeps it.")
    print("  From there, Start brings the companion up and the switcher picks who is live.")
    print("  (./start.sh still works from the terminal.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
