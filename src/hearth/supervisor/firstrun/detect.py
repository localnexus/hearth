"""firstrun/detect.py — is this install still on its first run?

Two facts, both read from disk at call time and neither ever cached:

  needs_model  the selected model.toml still carries the id the shipped
               template ships with (or none at all) — a bot started now would
               ask the server for a model that does not exist;
  fresh        no companion under the data root has a session file yet —
               nothing has been said on this install.

Either one makes the launch page offer the first-run walk; only the first
parks its Start. Names and booleans only: the model id is a model name, never
a secret, and no file content beyond that one key is read out.

One part of the /admin/first-run surface; the package __init__ carries the map
of the whole and re-exports every name defined here.
"""

from __future__ import annotations

from pathlib import Path

from .. import switch as switch_mod


def selection() -> dict | None:
    """The selection keys from active.toml, or None when the file is absent or
    unreadable (the bootstrap has not run, or the file is broken)."""
    current, err = switch_mod.read_selection()
    if not current or err:
        return None
    return {k: current.get(k) for k in switch_mod.SELECTION_KEYS if k in current}


def model_path(model_name: str) -> Path:
    """The model.toml the selection names — data root first, shipped fallback."""
    from hearth.config import config_loader  # lazy: mirrors the package gate idiom

    return config_loader.model_dir(model_name) / "model.toml"


def model_facts(sel: dict) -> dict:
    """{name, id, id_set}. id_set is False for the placeholder AND for an
    unreadable or id-less file — every one of them means 'not chosen yet'."""
    from hearth.init import PLACEHOLDER_ID, InitError, current_model_id  # lazy

    name = str(sel.get("model") or "")
    try:
        mid = current_model_id(model_path(name)) if name else ""
    except (InitError, OSError):
        mid = ""
    return {"name": name or None, "id": mid or None,
            "id_set": bool(mid) and mid != PLACEHOLDER_ID}


def is_fresh() -> bool:
    """True until any companion under the data root has a session file."""
    from hearth.session import session_store  # lazy

    return not any(any(d.glob("*.json")) for d in session_store.all_sessions_dirs())


def detect() -> dict | None:
    """{"needs_model", "fresh"} for /admin/state's poll. Never raises: an
    install too broken to read answers None, and the launch page draws nothing."""
    try:
        sel = selection()
        needs = True if sel is None else not model_facts(sel)["id_set"]
        return {"needs_model": needs, "fresh": is_fresh()}
    except Exception:  # noqa: BLE001 — a poll must not die on a broken tree
        return None
