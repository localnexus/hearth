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
    print("  the server advertises several models:")
    for i, m in enumerate(ids, 1):
        print(f"    {i}. {m}")
    ans = input("  which one should the companion use? [number, or Enter to decide later] ").strip()
    if ans.isdigit() and 1 <= int(ans) <= len(ids):
        return ids[int(ans) - 1]
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hearth.init",
                                 description="first-run bootstrap: templates, token, gates")
    ap.add_argument("--yes", "-y", action="store_true",
                    help="take every default and ask nothing (memory stays off)")
    ap.add_argument("--memory", choices=("on", "off"),
                    help="enable cross-sitting memory without being asked (default: ask; off)")
    ap.add_argument("--lm-url", default=DEFAULT_LM_URL,
                    help=f"your OpenAI-compatible LLM server (default {DEFAULT_LM_URL})")
    ap.add_argument("--lm-token", default="",
                    help="bearer for that server, only if it wants one (never stored)")
    ap.add_argument("--model-id", help="model id to record, skipping the probe")
    ap.add_argument("--no-probe", action="store_true", help="do not contact the LLM server")
    args = ap.parse_args(argv)
    interactive = sys.stdin.isatty() and not args.yes

    rep = Report()
    print(f"Hearth first run — data root: {cl.DATA_DIR}")
    try:
        paths = copy_templates(rep)
        mint_token(paths["serve"], rep)
        open_gates(paths["serve"], rep)
        if args.lm_url != DEFAULT_LM_URL:
            set_lm_url(paths["serve"], args.lm_url, rep)

        want_memory = (args.memory == "on") if args.memory else _ask_yes_no(
            "Should the companion remember across sittings? This writes records under "
            f"{cl.DATA_DIR / 'characters'}/<companion>/memory/ — local, 0600, deletable.",
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
                rep.add("note", f"no LLM server answering at {args.lm_url} — the model id "
                                "is left for later")
            elif not ids:
                rep.add("note", f"{args.lm_url} answers but advertises no model yet")
            else:
                chosen = _pick(ids, interactive)
                if chosen is None:
                    rep.add("note", f"{len(ids)} models advertised — pick one on the launch "
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
        print(f"  your bearer token (shown once — it lives in the file named above):")
        print(f"    {rep.token}")
    placeholder = current_model_id(model_path) == PLACEHOLDER_ID
    print()
    print("Next:")
    print("  1. start the facade:   .venv/bin/python -m hearth.serve")
    print(f"  2. open                {facade_url(paths['serve'])}")
    print("     and paste the token when the page asks — once; that browser keeps it.")
    print("  From there Start brings the voice loop up and the companion switcher picks who")
    print("  is live. (./start.sh is the terminal path and still works.)")
    if placeholder:
        print(f"  ! model id is still \"{PLACEHOLDER_ID}\" — set it to what your server")
        print("    advertises before the first turn (settings page, or "
              f"{model_path.relative_to(cl.DATA_DIR) if model_path.is_relative_to(cl.DATA_DIR) else model_path}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
