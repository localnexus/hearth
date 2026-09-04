"""roster/forms.py — what the two clip verbs refuse: names, provenance,
personas, tiers.

The two check functions are mirror images, which is why they sit together:
onboarding requires the character NOT to exist (create-only), add-a-voice
requires it TO exist and the TAG not to (create-only per tag). Both demand a
source attestation — where a clip came from cannot be mechanized.

_validate_persona_text runs compose_persona's contract against SUBMITTED text
with no file in play: the same section regex, so the form refuses what the
loader would refuse. It is a pre-check, not the proof — every write is
verified afterwards by the real startup loader and rolled back if that fails.

One part of the /admin/roster arc; the package __init__ carries the map of the
whole and re-exports every name defined here.
"""

from __future__ import annotations

import re

from .. import switch as switch_mod

_TIERS = ("", "floor", "hindsight")  # "" = don't touch memory.toml


def _validate_persona_text(text: str) -> str | None:
    """The compose_persona contract on SUBMITTED text (same regex, no file):
    '## IDENTITY' + '## SOUL', both non-empty after comment stripping."""
    from hearth.config import config_loader

    stripped = config_loader._strip_comments(text or "")
    parts = re.split(r"^##\s+([A-Za-z]+)\s*$", stripped, flags=re.MULTILINE)
    sections = {parts[i].strip().upper(): parts[i + 1].strip()
                for i in range(1, len(parts) - 1, 2)}
    for required in ("IDENTITY", "SOUL"):
        if not sections.get(required):
            return f"persona needs a non-empty '## {required}' section"
    return None


def _known_characters() -> set[str]:
    return {c["name"] for c in switch_mod.choices()["characters"]}


def _check_fields(form: dict) -> tuple[dict, list[str]]:
    """(cleaned fields, errors). Names dir-safe; provenance answered; tier known."""
    from hearth.config import config_loader

    errors: list[str] = []
    name = str(form.get("name") or "").strip()
    tag = str(form.get("voice_tag") or "default").strip()
    for label, value in (("character name", name), ("voice tag", tag)):
        if not config_loader._NAME_RE.match(value) or value.startswith("."):
            errors.append(f"invalid {label} (letters, digits, . _ - only)")
    if name and name in _known_characters():
        errors.append(f"character {name!r} already exists — the wizard is "
                      "create-only (persona edits are a later surface)")
    persona_err = _validate_persona_text(str(form.get("persona") or ""))
    if persona_err:
        errors.append(persona_err)
    license_ = str(form.get("license") or "").strip() or "personal-use-only"
    source = str(form.get("source") or "").strip()
    if not source:
        errors.append("source attestation is required — where the clip came "
                      "from cannot be mechanized")
    tier = str(form.get("memory_tier") or "").strip()
    if tier not in _TIERS:
        errors.append(f"unknown memory tier {tier!r}")
    return ({"name": name, "tag": tag, "license": license_, "source": source,
             "tier": tier, "persona": str(form.get("persona") or "")}, errors)


def _check_voice_fields(form: dict) -> tuple[dict, list[str]]:
    """(cleaned fields, errors) for the add-voice verb: the character must
    EXIST (the wizard's inverse), the TAG must not (per-tag create-only)."""
    from hearth.config import config_loader

    errors: list[str] = []
    name = str(form.get("character") or "").strip()
    tag = str(form.get("voice_tag") or "").strip()
    if name not in _known_characters():
        errors.append(f"unknown character {name!r} — add-a-voice needs an "
                      "existing one (the wizard onboards new characters)")
    if not config_loader._NAME_RE.match(tag) or tag.startswith("."):
        errors.append("invalid voice tag (letters, digits, . _ - only)")
    elif name in _known_characters() and tag in config_loader.list_voices(name):
        errors.append(f"voice {tag!r} already exists for {name!r} — add-a-voice "
                      "is create-only per tag (bundles are never overwritten)")
    license_ = str(form.get("license") or "").strip() or "personal-use-only"
    source = str(form.get("source") or "").strip()
    if not source:
        errors.append("source attestation is required — where the clip came "
                      "from cannot be mechanized")
    return ({"name": name, "tag": tag, "license": license_, "source": source},
            errors)
