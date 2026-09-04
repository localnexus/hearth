"""settings_registry/knobs.py — the single-source knob surfaces, the x-hearth
stamp factories, and _Cfg.

Sliced out of the single settings_registry.py it used to share; see the
package __init__ for the layout and the order the parts import in.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from hearth.tts import paralinguistics as _paralinguistics

_NAME = r"^[A-Za-z0-9._-]+$"  # dir-name safe (config_loader._NAME_RE)

# ── single-source knob surfaces (the live modules import these) ───────────────
TEMP_CEILING = 1.4  # highest ear-verified-clean synth temperature (2026-08-26 ear session)
ENGINE_LIVE_KNOBS: dict[str, frozenset[str]] = {
    # Synth knobs each engine HONORS live; proper Chatterbox adds the two
    # emotion knobs (EXPENSIVE tier — engine swap not wired).
    "chatterbox-turbo": frozenset({"temperature", "top_p", "top_k", "repetition_penalty"}),
    "chatterbox": frozenset({"temperature", "top_p", "top_k", "repetition_penalty",
                             "exaggeration", "cfg_weight"}),
}
TURBO_LIVE_KNOBS = ENGINE_LIVE_KNOBS["chatterbox-turbo"]
SERVE_SPEECH_KNOBS = TURBO_LIVE_KNOBS | {"speed"}  # the facade speech layer adds speed
# The tag vocabulary derives the OTHER way — it lives with its stem-behavior
# table (a name list cannot generate behavior); bracketless here.
CANONICAL_TAGS = frozenset(t[1:-1] for t in _paralinguistics._CANONICAL)


def _live(hot_via: str, status: str | None = None) -> dict:
    """x-hearth extra for a field with a live path: overrides.toml for the
    knob tiers; the supervisor's switch intent for the selection fields."""
    return {"x-hearth": {"hot_via": hot_via, "status_source": status}}


def _secret() -> dict:
    """x-hearth extra for a field whose VALUE must never leave the file through
    a display surface (paths and key names are fine; values are not). The
    settings routes redact these on read and refuse to write them."""
    return {"x-hearth": {"secret": True}}


def _effect(restart: str, note: str = "", secret: bool = False) -> dict:
    """x-hearth extra for a field with NO live path: the effect-time stamp
    (audit 2026-09-02) — what must relaunch for a persisted edit to land.
    Vocabulary matches FileEntry.restart ("bot+facade" | "facade" | ...).

    Verified against the code, not assumed: both lanes snapshot [memory] at
    process boot (memory.maybe_attach loads the file at bot start;
    serve ServeMemory.__init__ caches it at facade boot and builds every
    per-session seam and per-companion backend from that snapshot), and
    MemorySeam.__init__ freezes recall/per_turn values into seam attributes.
    The per-turn gates are CONSULTED every turn (memory_prefetch, chat glue)
    but never re-read from disk — so nothing under [memory] is hot FROM THE
    FILE. One sanctioned runtime exception: the panel's per-turn-voice pause
    (POST /memory/per-turn-voice) pokes the live seam's voice gate without
    touching this file — the file stays the between-sessions truth."""
    x: dict = {"hot_via": None, "effect": restart}
    if note:
        x["effect_note"] = note
    if secret:
        x["secret"] = True
    return {"x-hearth": x}


class _Cfg(BaseModel):
    """Base for every file model: unknown keys tolerated at validation (they are
    reported separately, as warnings — never a hard stop)."""
    model_config = ConfigDict(extra="allow")

