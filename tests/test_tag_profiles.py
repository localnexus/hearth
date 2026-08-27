"""test_tag_profiles.py — the tag-envelope profile matrix (loader + detection).

Runs headless on the REAL config artifact (config/tts/chatterbox-turbo/tts.toml)
plus synthetic tables for the validation edges.

Run: .venv/bin/python test_tag_profiles.py   (exit 0 = all pass)
"""
from hearth.tts import tag_profiles as TP

_PASS = 0
_FAIL = 0


def check(cond: bool, msg: str) -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  PASS  {msg}")
    else:
        _FAIL += 1
        print(f"  FAIL  {msg}")


print("=" * 66)
print("test_tag_profiles.py — envelope profile matrix")
print("=" * 66)

# ── 1. REAL ARTIFACT — the shipped seeded profiles load and validate clean
print("\n[1] REAL ARTIFACT (config/tts/chatterbox-turbo/tts.toml)")
real = TP.load_profiles("chatterbox-turbo")
for tag in ("crying", "happy", "surprised", "fear", "angry", "sarcastic"):
    check(real.get(tag) == {"temperature": 1.2}, f"seeded {tag} -> temperature 1.2")
for tag in ("whispering", "dramatic", "narration", "advertisement"):
    check(tag not in real, f"{tag} deliberately has NO profile entry")
check(TP.load_profiles("no-such-engine") == {}, "unknown engine -> {} (fail-soft)")

# ── 2. DETECTION — deltas_for on post-normalize text
print("\n[2] DETECTION (deltas_for)")
P = {"crying": {"temperature": 1.2}, "fear": {"temperature": 1.2, "top_p": 0.9}}
check(TP.deltas_for("[crying] He's gone.", P) == {"temperature": 1.2},
      "leading profiled tag -> its deltas")
check(TP.deltas_for("I was fine. [crying] And then I wasn't.", P) == {"temperature": 1.2},
      "mid-chunk profiled tag -> its deltas")
check(TP.deltas_for("Plain sentence, no tags.", P) == {}, "untagged -> {}")
check(TP.deltas_for("[sigh] Anyway.", P) == {}, "unprofiled canonical tag -> {}")
check(TP.deltas_for("[dramatic] This is it.", P) == {}, "profiled-elsewhere tag absent from table -> {}")
check(TP.deltas_for("[crying] then [fear] both.", P) == {"temperature": 1.2, "top_p": 0.9},
      "two profiled tags merge, last-wins per key")
check(TP.deltas_for("", P) == {}, "empty text -> {}")
check(TP.deltas_for("[crying] x", {}) == {}, "empty profile table -> {}")

# ── 3. VALIDATION EDGES — via a synthetic table written to a temp engine dir
print("\n[3] VALIDATION (synthetic engine table)")
import shutil
tmp_engine = "tmp-test-engine"
tmp_dir = TP.TTS_DIR / tmp_engine
tmp_dir.mkdir(parents=True, exist_ok=True)
(tmp_dir / "tts.toml").write_text(
    "[tag_profiles.crying]\n"
    "temperature = 2.0\n"            # over ceiling -> clamped to 1.4
    "exaggeration = 0.7\n"           # not honored by turbo-key-set -> dropped
    "top_k = true\n"                 # bool masquerading as number -> dropped
    "[tag_profiles.notatag]\n"       # unknown tag -> whole entry dropped
    "temperature = 1.0\n"
    "[tag_profiles.sigh]\n"          # canonical breath cue MAY carry a profile
    "top_p = 0.9\n"
)
TP._ALLOWED_KNOBS[tmp_engine] = TP._ALLOWED_KNOBS["chatterbox-turbo"]
syn = TP.load_profiles(tmp_engine)
check(syn.get("crying") == {"temperature": 1.4}, "over-ceiling temperature clamped to 1.4")
check("notatag" not in syn, "non-canonical tag dropped")
check(syn.get("sigh") == {"top_p": 0.9}, "breath-cue tag accepted (canonical set governs)")
del TP._ALLOWED_KNOBS[tmp_engine]
shutil.rmtree(tmp_dir)

# ── 4. FACADE PRECEDENCE — with_tag_profile pin/allow semantics
print("\n[4] FACADE PRECEDENCE (tts_prep.with_tag_profile)")
import sys
sys.path.insert(0, ".")
from hearth.serve import tts_prep


class _Deps:
    def __init__(self, pinned, allow):
        self.pinned_tts = pinned
        self.allow_tag_profiles = allow


base = {"model": "m", "input": "[crying] He's gone.", "response_format": "wav",
        "ref_audio": "/x.wav", "temperature": 0.79}
# no pin: deltas apply
out = tts_prep.with_tag_profile(dict(base), _Deps({}, True))
check(out["temperature"] == 1.2, "no pin -> envelope applies (0.79 -> 1.2)")
# pin without opt-in: pin re-asserted, payload unchanged
out = tts_prep.with_tag_profile(dict(base), _Deps({"temperature": 0.79}, False))
check(out["temperature"] == 0.79, "pin without opt-in -> pin wins, delta swallowed")
# pin WITH opt-in: envelope overlays the pin
out = tts_prep.with_tag_profile(dict(base), _Deps({"temperature": 0.79}, True))
check(out["temperature"] == 1.2, "pin with allow_tag_profiles -> envelope overlays")
# untagged input: payload identity (the common case is free)
plain = dict(base, input="Plain words only.")
out = tts_prep.with_tag_profile(plain, _Deps({}, True))
check(out is plain, "untagged input -> same payload object (no copy)")

print("\n" + "=" * 66)
print(f"  {_PASS} passed, {_FAIL} failed")
print("=" * 66)
raise SystemExit(1 if _FAIL else 0)
