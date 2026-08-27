"""test_config_profiles.py — features/config_profiles.py preset helpers (pure + IO).

Runnable directly (repo convention — no pytest in venv):

    uv run python test_config_profiles.py

Covers the two-tier segmentation logic: snapshot (save), compose_load (REPLACE tier +
voice clip selection), compose_reset (per-tier / all), target validation & name-safety,
and a profile-file IO round-trip. Does NOT start the web server — the route handlers are
thin shells over these pure functions. Also asserts the seam registers both contributors.
"""

import tempfile
import tomllib
from pathlib import Path

from hearth.control.features import config_knobs as ck
from hearth.control.features import config_profiles as cp
from hearth.control.features.config_profiles import ProfileError, _compose_load, _compose_reset, _snapshot

_PASS = 0
_FAIL = 0


def check(name: str, cond: bool) -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ok   {name}")
    else:
        _FAIL += 1
        print(f"  FAIL {name}")


def rejects(name: str, fn) -> None:
    try:
        fn()
    except ProfileError:
        check(name, True)
    except Exception as exc:  # wrong type
        check(f"{name} (raised {type(exc).__name__}, wanted ProfileError)", False)
    else:
        check(f"{name} (did not raise)", False)


def rejects_knob(name: str, fn) -> None:
    """Assert fn() raises config_knobs.KnobError (the merge/validate layer)."""
    try:
        fn()
    except ck.KnobError:
        check(name, True)
    except Exception as exc:
        check(f"{name} (raised {type(exc).__name__}, wanted KnobError)", False)
    else:
        check(f"{name} (did not raise)", False)


# A real voice bundle (existence is checked when a ref_wav lands in overrides).
_REF = "characters/example/voices/default/sample.wav"


def test_snapshot():
    print("test_snapshot")
    cur = {"llm": {"temperature": 0.9, "reasoning_effort": "low"}, "tts": {"top_p": 0.8}}
    check("character snapshot = [llm] only", _snapshot("character", cur) == {"llm": {"temperature": 0.9, "reasoning_effort": "low"}})
    check("voice snapshot = [tts] only", _snapshot("voice", cur) == {"tts": {"top_p": 0.8}})
    # snapshot of an absent tier → empty section (a valid baseline preset)
    check("empty tier snapshots empty", _snapshot("character", {"tts": {"top_p": 0.8}}) == {"llm": {}})


def test_compose_load():
    print("test_compose_load")
    # character load REPLACES [llm], preserves [tts]
    cur = {"llm": {"temperature": 0.5, "reasoning_effort": "high"}, "tts": {"top_p": 0.9}}
    prof = {"llm": {"temperature": 0.8}}
    out = _compose_load("character", cur, prof)
    check("char load replaces llm", out["llm"] == {"temperature": 0.8})
    check("char load keeps tts", out["tts"] == {"top_p": 0.9})

    # empty character profile → tier cleared to baseline (section pruned)
    out = _compose_load("character", cur, {})
    check("empty char profile clears llm", "llm" not in out and out["tts"] == {"top_p": 0.9})

    # voice load REPLACES [tts], sets ref_wav, keeps [llm]
    cur = {"llm": {"temperature": 0.5}, "tts": {"top_p": 0.5, "top_k": 500}}
    prof = {"tts": {"top_p": 0.95, "repetition_penalty": 1.3}}
    out = _compose_load("voice", cur, prof, ref_wav=_REF)
    check("voice load replaces tts", out["tts"] == {"top_p": 0.95, "repetition_penalty": 1.3})
    check("voice load sets ref_wav", out["voice"]["ref_wav"] == _REF)
    check("voice load keeps llm", out["llm"] == {"temperature": 0.5})

    # empty voice profile still selects the clip, clears tts
    out = _compose_load("voice", cur, {}, ref_wav=_REF)
    check("empty voice profile clears tts", "tts" not in out)
    check("empty voice profile still sets clip", out["voice"]["ref_wav"] == _REF)

    # a corrupted profile value is REJECTED by the merge/validate path (KnobError,
    # which the route handler catches as ck.KnobError → HTTP 400).
    rejects_knob("load rejects bad profile value",
                 lambda: _compose_load("voice", {}, {"tts": {"top_p": 9.9}}, ref_wav=_REF))
    rejects_knob("load rejects missing clip",
                 lambda: _compose_load("voice", {}, {}, ref_wav="characters/example/voices/nope/x.wav"))


def test_compose_reset():
    print("test_compose_reset")
    cur = {"llm": {"temperature": 0.9}, "tts": {"top_p": 0.8}, "voice": {"ref_wav": _REF}}
    check("reset character drops llm", _compose_reset("character", cur) == {"tts": {"top_p": 0.8}, "voice": {"ref_wav": _REF}})
    check("reset voice drops tts only", _compose_reset("voice", cur) == {"llm": {"temperature": 0.9}, "voice": {"ref_wav": _REF}})
    check("reset all → empty", _compose_reset("all", cur) == {})


def test_vad_taxonomy():
    """Calibration never travels with texture: character/voice profile ops
    must neither capture nor disturb [vad]; only 'all' clears it (its label promises ALL)."""
    print("test_vad_taxonomy")
    cur = {"llm": {"temperature": 0.9}, "tts": {"top_p": 0.8}, "vad": {"stop_secs": 0.8}}
    check("char snapshot excludes vad", "vad" not in _snapshot("character", cur))
    check("voice snapshot excludes vad", "vad" not in _snapshot("voice", cur))
    out = _compose_load("character", cur, {"llm": {"temperature": 0.7}})
    check("char load preserves vad", out["vad"] == {"stop_secs": 0.8})
    out = _compose_load("voice", cur, {"tts": {"top_p": 0.95}}, ref_wav=_REF)
    check("voice load preserves vad", out["vad"] == {"stop_secs": 0.8})
    check("reset character preserves vad", _compose_reset("character", cur)["vad"] == {"stop_secs": 0.8})
    check("reset voice preserves vad", _compose_reset("voice", cur)["vad"] == {"stop_secs": 0.8})
    check("reset all clears vad too", _compose_reset("all", cur) == {})


def test_validation():
    print("test_validation")
    rejects("bad scope", lambda: cp._validate_target("bogus", "example", None))
    rejects("unknown character", lambda: cp._validate_target("character", "no-such-char", None))
    rejects("voice scope needs voice", lambda: cp._validate_target("voice", "example", None))
    rejects("unknown voice bundle", lambda: cp._validate_target("voice", "example", "nope"))
    rejects("path traversal blocked", lambda: cp._safe("../etc", "character"))
    # valid targets do not raise
    cp._validate_target("all", "", None)
    cp._validate_target("character", "example", None)
    cp._validate_target("voice", "example", "default")
    check("valid targets accepted", True)


def test_io_roundtrip():
    print("test_io_roundtrip")
    with tempfile.TemporaryDirectory() as d:
        orig = cp._PROFILES
        cp._PROFILES = Path(d) / "profiles"
        try:
            p = cp._char_path("example")
            cp._atomic_write(p, ck._dump({"llm": {"temperature": 0.8, "reasoning_effort": "low"}}))
            check("profile reads back", cp._read_profile(p) == {"llm": {"temperature": 0.8, "reasoning_effort": "low"}})
            check("gitignore dropped", (cp._PROFILES / ".gitignore").exists())
            check("gitignore ignores all", "*" in (cp._PROFILES / ".gitignore").read_text())
            check("absent profile → {}", cp._read_profile(cp._voice_path("example", "default")) == {})
            check("no stray .tmp", not p.with_name(p.name + ".tmp").exists())
        finally:
            cp._PROFILES = orig


def test_seam_registration():
    print("test_seam_registration")
    from hearth.control import control_routes
    names = {fn.__name__ for fn in control_routes.contributors()}
    check("config_knobs registered", "config_knob_routes" in names)
    check("config_profiles registered", "config_profile_routes" in names)


if __name__ == "__main__":
    test_snapshot()
    test_compose_load()
    test_compose_reset()
    test_vad_taxonomy()
    test_validation()
    test_io_roundtrip()
    test_seam_registration()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    raise SystemExit(1 if _FAIL else 0)
