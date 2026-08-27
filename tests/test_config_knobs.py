"""test_config_knobs.py — features/config_knobs.py write-path (pure helpers + IO round-trip).

Runnable directly (repo convention — no pytest in venv):

    uv run python test_config_knobs.py

Tests the decoupled write-path: validation of the honored surface, merge/clear/revert
semantics, the tiny TOML serializer (round-tripped through stdlib tomllib), and an atomic
IO round-trip against a temp file. Does NOT start the web server — the route handlers are
thin shells over these pure functions.
"""

import tempfile
import tomllib
from pathlib import Path

from hearth.control.features import config_knobs as ck
from hearth.control.features.config_knobs import KnobError, _dump, _merge

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
    """Assert fn() raises KnobError."""
    try:
        fn()
    except KnobError:
        check(name, True)
    except Exception as exc:  # wrong exception type
        check(f"{name} (raised {type(exc).__name__}, wanted KnobError)", False)
    else:
        check(f"{name} (did not raise)", False)


def roundtrip(data: dict) -> dict:
    """dump → parse back with stdlib tomllib; proves the serializer emits valid TOML."""
    return tomllib.loads(_dump(data))


# A voice ref that actually exists (existence is validated at write time).
_REAL_REF = "characters/example/voices/default/sample.wav"


def test_validate_and_merge():
    print("test_validate_and_merge")
    # set + coerce
    out = _merge({}, {"llm": {"temperature": 0.8, "reasoning_effort": "low"}})
    check("llm temp set", out["llm"]["temperature"] == 0.8)
    check("llm reasoning set", out["llm"]["reasoning_effort"] == "low")

    # tts int vs float
    out = _merge({}, {"tts": {"top_k": 900, "top_p": 0.9}})
    check("tts top_k int", out["tts"]["top_k"] == 900 and isinstance(out["tts"]["top_k"], int))
    check("tts top_p float", out["tts"]["top_p"] == 0.9)

    # preserve unrelated keys
    base = {"llm": {"temperature": 0.5}, "tts": {"top_p": 0.95}}
    out = _merge(base, {"llm": {"reasoning_effort": "high"}})
    check("preserve unrelated", out["llm"]["temperature"] == 0.5 and out["tts"]["top_p"] == 0.95)

    # null clears a key
    out = _merge({"llm": {"temperature": 0.9}}, {"llm": {"temperature": None}})
    check("null clears key", "llm" not in out)  # last key gone → section pruned

    # explicit clear list
    out = _merge({"llm": {"temperature": 0.9, "reasoning_effort": "low"}}, {"clear": ["llm.temperature"]})
    check("clear one key", out["llm"] == {"reasoning_effort": "low"})

    # voice ref: real path accepted, stored as the original (portable) ref
    out = _merge({}, {"voice": {"ref_wav": _REAL_REF}})
    check("voice ref stored verbatim", out["voice"]["ref_wav"] == _REAL_REF)


def test_rejections():
    print("test_rejections")
    rejects("reject system_instruction", lambda: _merge({}, {"llm": {"system_instruction": "hi"}}))
    rejects("reject inert tts key", lambda: _merge({}, {"tts": {"exaggeration": 1.0}}))
    rejects("reject unknown llm key", lambda: _merge({}, {"llm": {"nope": 1}}))
    rejects("reject unknown section", lambda: _merge({}, {"bogus": {"x": 1}}))
    rejects("reject unknown vad key", lambda: _merge({}, {"vad": {"nope": 0.5}}))
    rejects("reject vad out of range", lambda: _merge({}, {"vad": {"stop_secs": 9.0}}))
    rejects("reject temp out of range", lambda: _merge({}, {"llm": {"temperature": 9.0}}))
    rejects("reject bool as number", lambda: _merge({}, {"llm": {"temperature": True}}))
    rejects("reject bad reasoning enum", lambda: _merge({}, {"llm": {"reasoning_effort": "extreme"}}))
    rejects("reject missing voice ref", lambda: _merge({}, {"voice": {"ref_wav": "characters/nope/x.wav"}}))
    rejects("reject oversized persona", lambda: _merge({}, {"llm": {"persona": "x" * (ck._PERSONA_MAX + 1)}}))
    rejects("reject non-table section", lambda: _merge({}, {"llm": "not a dict"}))


def test_vad_tier():
    """The CALIBRATION tier's write surface."""
    print("test_vad_tier")
    out = _merge({}, {"vad": {"stop_secs": 0.8, "confidence": 0.6}})
    check("vad knobs set", out["vad"] == {"stop_secs": 0.8, "confidence": 0.6})
    # the panel's Reset-listening path: dotted clears
    out2 = _merge(out, {"clear": ["vad.stop_secs"]})
    check("vad dotted clear", out2["vad"] == {"confidence": 0.6})
    out3 = _merge(out, {"clear": ["vad.stop_secs", "vad.confidence"]})
    check("vad full clear prunes section", "vad" not in out3)
    check("vad in schema",
          set(ck._SCHEMA["vad"]) == {"confidence", "start_secs", "stop_secs", "min_volume"})
    check("vad round-trips", roundtrip(out) == out)
    # write surface ≡ honored surface, vad flavor (same guard as the tts mirror)
    from hearth.config import config_reload
    check("vad ranges mirror reloader keys", set(ck._VAD_RANGES) == set(config_reload._VAD_KEYS))


def test_dump_roundtrip():
    print("test_dump_roundtrip")
    # empty → header-only no-op parses to {}
    check("empty dumps to no-op", roundtrip({}) == {})

    data = _merge({}, {"llm": {"temperature": 0.7, "reasoning_effort": "medium"},
                       "tts": {"top_k": 1000, "repetition_penalty": 1.2}})
    check("full dict round-trips", roundtrip(data) == data)

    # persona with quotes + newline survives serialization
    tricky = _merge({}, {"llm": {"persona": 'She said "hi".\nThen left.\tOK'}})
    check("tricky persona round-trips", roundtrip(tricky) == tricky)

    # deterministic section order (llm before tts)
    text = _dump({"tts": {"top_p": 0.9}, "llm": {"temperature": 0.5}})
    check("section order deterministic", text.index("[llm]") < text.index("[tts]"))


def test_io_roundtrip():
    print("test_io_roundtrip")
    with tempfile.TemporaryDirectory() as d:
        orig = ck._OVERRIDES
        ck._OVERRIDES = Path(d) / "overrides.toml"
        try:
            check("read absent → {}", ck._read() == {})
            new = _merge(ck._read(), {"llm": {"temperature": 0.8}})
            ck._atomic_write(_dump(new))
            check("write then read back", ck._read() == new)
            check("no stray .tmp left", not (Path(d) / "overrides.toml.tmp").exists())
            # clear everything → header-only file, reads as {}
            ck._atomic_write(_dump(_merge(ck._read(), {"llm": {"temperature": None}})))
            check("cleared file reads {}", ck._read() == {})
        finally:
            ck._OVERRIDES = orig


def test_scrub_session_scoped():
    """[voice].ref_wav must not outlive the session; everything else must."""
    print("test_scrub_session_scoped")
    with tempfile.TemporaryDirectory() as d:
        orig = ck._OVERRIDES
        ck._OVERRIDES = Path(d) / "overrides.toml"
        try:
            ck._atomic_write(_dump({"voice": {"ref_wav": _REAL_REF},
                                    "tts": {"top_p": 0.9}, "vad": {"stop_secs": 0.8}}))
            check("ref_wav scrubbed", ck.scrub_session_scoped() == ["voice.ref_wav"])
            after = ck._read()
            check("other overrides preserved", after == {"tts": {"top_p": 0.9},
                                                         "vad": {"stop_secs": 0.8}})
            check("emptied voice section pruned", "voice" not in after)
            check("second scrub is a no-op", ck.scrub_session_scoped() == [])
            ck._OVERRIDES.unlink()
            check("absent file → no-op, none created",
                  ck.scrub_session_scoped() == [] and not ck._OVERRIDES.exists())
            ck._OVERRIDES.write_text("not [ valid toml", encoding="utf-8")
            check("malformed file left untouched",
                  ck.scrub_session_scoped() == []
                  and ck._OVERRIDES.read_text(encoding="utf-8") == "not [ valid toml")
            ck._OVERRIDES.write_text('[voice]\nref_wav = "x"\n\n[custom]\na = 1\n',
                                     encoding="utf-8")
            check("unknown section → skip (hand-edit preserved)",
                  ck.scrub_session_scoped() == [] and "custom" in ck._read())
        finally:
            ck._OVERRIDES = orig


if __name__ == "__main__":
    test_validate_and_merge()
    test_rejections()
    test_vad_tier()
    test_dump_roundtrip()
    test_io_roundtrip()
    test_scrub_session_scoped()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    raise SystemExit(1 if _FAIL else 0)
