"""test_paralinguistics.py — the repair/non-destructive/idempotency matrix.

Run: .venv/bin/python test_paralinguistics.py   (exit 0 = all pass)
"""
import unittest

from hearth.tts import paralinguistics as P

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


# ── 1. REPAIR — enclosed bare cue root (any wrapper × case/padding/morphology) → canonical
REPAIR = [
    # laugh
    ("*laughs*", "[laugh]"), ("(laughing)", "[laugh]"), ("[LAUGHED]", "[laugh]"), ("{laugh}", "[laugh]"),
    # chuckle (silent-e morphology)
    ("*chuckles*", "[chuckle]"), ("[chuckling]", "[chuckle]"), ("(chuckled)", "[chuckle]"),
    # sigh
    ("*sigh*", "[sigh]"), ("( sigh )", "[sigh]"), ("[SIGHS]", "[sigh]"), ("{sighing}", "[sigh]"),
    # gasp
    ("*gasps*", "[gasp]"), ("(gasped)", "[gasp]"),
    # groan (+ the `moan` alias → [groan], every morph, every enclosure)
    ("*groaning*", "[groan]"), ("[groans]", "[groan]"),
    ("*moans*", "[groan]"), ("(moaning)", "[groan]"), ("[MOANED]", "[groan]"), ("{moan}", "[groan]"),
    ("*murmurs*", "[groan]"), ("(murmuring)", "[groan]"), ("[MURMURED]", "[groan]"), ("{murmur}", "[groan]"),
    # sniff (incl. sniffle)
    ("*sniffs*", "[sniff]"), ("(sniffle)", "[sniff]"), ("[sniffling]", "[sniff]"),
    # exact brace tags observed from a session — repair salvages brace cue roots
    ("{chuckle}", "[chuckle]"), ("{laugh}", "[laugh]"), ("{sniff}", "[sniff]"),
    # cough
    ("*coughs*", "[cough]"), ("(coughing)", "[cough]"),
    # shush
    ("*shushes*", "[shush]"), ("[shushing]", "[shush]"), ("(shush)", "[shush]"),
    # clear throat (multi-word canonical + possessive/word-order variants)
    ("*clears throat*", "[clear throat]"), ("(clearing throat)", "[clear throat]"),
    ("[clears his throat]", "[clear throat]"), ("{throat clearing}", "[clear throat]"),
    ("*clear throat*", "[clear throat]"),
    # curated multi-word tag-attempts (deliberate +1-word whitelist)
    ("[soft sigh]", "[sigh]"), ("[ soft sigh ]", "[sigh]"), ("(soft sighs)", "[sigh]"),
    ("*softly sighing*", "[sigh]"), ("[SOFT SIGH]", "[sigh]"),
    ("...I know. [soft sigh] It's alright.", "...I know. [sigh] It's alright."),
    # embedded — position preserved
    ("Well, *sighs* I guess.", "Well, [sigh] I guess."),
    ("Ha! (chuckles) You got me.", "Ha! [chuckle] You got me."),
    # pilot style tokens — canonical = the exact trained token;
    # morphology/enclosure variants repair up to it
    ("[whisper]", "[whispering]"), ("*whispers*", "[whispering]"), ("{whispered}", "[whispering]"),
    ("(crying)", "[crying]"), ("[cry]", "[crying]"),
    ("[surprising]", "[surprised]"), ("(surprise)", "[surprised]"),
    ("{angry}", "[angry]"), ("[HAPPY]", "[happy]"),
    ("Come closer. *whispers* it's a secret.", "Come closer. [whispering] it's a secret."),
]

# ── 2. NON-DESTRUCTIVE — must pass through UNCHANGED
UNCHANGED = [
    "*sighs heavily*",              # +1 word (modifier) crosses the threshold
    "*pulls you into a hug*",       # stage-direction action, no canonical root
    "*clears his throat twice*",    # extra word beyond the clear-throat expression
    "(sarcastically)",              # non-square: catch-all is BRACKET-ONLY
    "She sighed and left.",         # unwrapped prose
    "The dog coughs a lot.",        # unwrapped prose
    "*sigh)",                       # mismatched enclosure
    "(see the note)",               # non-cue parenthetical
    "*gentle smile*",               # strip skips asterisks — RP action survives (repair-only)
    "(gentle pause)",               # strip skips parens — beat survives (repair-only)
    "{sigh]",                       # mismatched brace/bracket — no matching close, untouched
    "[laugh}",                      # mismatched bracket/brace — untouched
    "",                             # empty
]

# ── 2b. STRIP — every bracketed non-cue removed (catch-all, post-repair)
STRIP = [
    # known families (smile/pause) — already-adjudicated
    ("[gentle smile]", ""),
    ("[gentle pause]", ""),
    ("[smile]", ""),
    ("[SOFT SMILE]", ""),
    ("[dramatic pause]", ""),
    ("[pauses]", ""),
    ("...I know. [gentle smile] It's alright.", "...I know. It's alright."),
    ("Well [gentle pause] anyway, we should go.", "Well anyway, we should go."),
    # CATCH-ALL — no per-tag rule needed, and none of these were
    # predicted. Previously these survived to Chatterbox and were SPOKEN ALOUD.
    ("[softly]", ""),               # a manner adverb — no cue means "quietly";
                                    # mapping to [shush] would voice an actual "shhh"
    ("[warmly]", ""),               # was UNCHANGED pre-catch-all → voiced "warmly"
    ("[soft chuckle]", ""),         # not whitelisted in _PHRASES → stripped, and
                                    # logged UNKNOWN so the chuckle can be curated
    ("[quietly]", ""), ("[teasingly]", ""),
    ("[beat]", ""), ("[sic]", ""),
    ("She's right. [softly] I know.", "She's right. I know."),
    # BRACE catch-all — some models drift to `{cue}` not `[cue]`.
    # Repair salvages brace CUE roots ({chuckle}->[chuckle]); a SURVIVING brace is a
    # non-cue that previously reached Chatterbox and was SPOKEN ALOUD.
    ("{softly}", ""), ("{soft chuckle}", ""), ("{grins}", ""), ("{warmly}", ""),
    ("She's right. {softly} I know.", "She's right. I know."),
    # mixed: brace cue repaired+kept, brace non-cue stripped, in one line
    ("{chuckle} come here {grins} now.", "[chuckle] come here now."),
]

# ── 2c. STRIP REPORT — the discovery signal: known vs UNKNOWN classification
REPORT = [
    ("[gentle smile]", [("[gentle smile]", True)]),     # adjudicated family → quiet
    ("[dramatic pause]", [("[dramatic pause]", True)]),
    ("[softly]", [("[softly]", False)]),                # novel → surfaces
    ("[warmly]", [("[warmly]", False)]),
    ("[soft chuckle]", [("[soft chuckle]", False)]),
    ("[sigh]", []),                                     # canonical → not a strip
    ("[clear throat]", []),
    ("Hi [softly] there [gentle smile] ok", [("[softly]", False), ("[gentle smile]", True)]),
    ("*sighs heavily*", []),                            # asterisk → strip skips it
    ("{softly}", [("{softly}", False)]),                # brace-drift → always UNKNOWN (surfaces)
    ("{soft chuckle}", [("{soft chuckle}", False)]),    # brace known-families are bracket-only
    ("{chuckle}", []),                                  # brace cue → repaired, not a strip
    ("plain prose", []),
    ("", []),
]

# ── 2e. MARKDOWN BOLD — `**…**`/`***…***` markers stripped, words
# kept ("emphasis on real words"; doubles field-heard as an audible artifact in
# bolded list items, where single asterisks are inert). Single `*…*` stays
# untouched for the full three-way handler.
BOLD = [
    ("**For Example**", "For Example"),
    ("- **Breathing**: slow and steady.", "- Breathing: slow and steady."),
    ("***really***", "really"),
    ("**one** and **two**", "one and two"),
    ("A list: **a**, **b**, **c**.", "A list: a, b, c."),
    ("stray ** marker", "stray marker"),              # orphan run → space, re-tidied
    ("**sighs**", "sighs"),                           # bold first: prose word, not a cue
    ("**Bold** and *sighs* now.", "Bold and [sigh] now."),  # bold-strip + cue repair coexist
    ("*single* stays", "*single* stays"),             # singles untouched (full handler's job)
]

# ── 3. GOOD-PATH NO-OP — canonical forms untouched
NOOP = [
    "[sigh]", "[clear throat]", "[chuckle]",
    "I'm so tired. [sigh] Anyway, let's go.",
    # PILOT style tokens survive the catch-all as-is (previously stripped)
    "[whispering]", "[angry]", "[happy]", "[sarcastic]", "[crying]",
    "[surprised]", "[fear]", "[dramatic]", "[narration]", "[advertisement]",
    "[whispering] come here.",
]

print("=" * 66)
print("test_paralinguistics.py — cue-tag repair matrix")
print("=" * 66)

print("\n[1] REPAIR")
for src, want in REPAIR:
    got = P.normalize(src)
    check(got == want, f"{src!r} -> {got!r} (want {want!r})")

print("\n[2] NON-DESTRUCTIVE (unchanged)")
for src in UNCHANGED:
    got = P.normalize(src)
    check(got == src, f"{src!r} unchanged -> {got!r}")

print("\n[2b] STRIP (every bracketed non-cue removed — catch-all)")
for src, want in STRIP:
    got = P.normalize(src)
    check(got == want, f"{src!r} -> {got!r} (want {want!r})")

print("\n[2c] STRIP REPORT (known vs UNKNOWN — the discovery signal)")
for src, want in REPORT:
    _, strips = P.normalize_with_report(src)
    got = [(s["token"], s["known"]) for s in strips]
    check(got == want, f"{src!r} -> {got!r} (want {want!r})")

print("\n[2d] PURITY — the report never mutates across calls")
for src, want in REPORT:
    a = P.normalize_with_report(src)[1]
    b = P.normalize_with_report(src)[1]
    check(a == b and len(a) == len(want), f"{src!r} stable report: {a!r}")

print("\n[2e] MARKDOWN BOLD (markers stripped, words kept)")
for src, want in BOLD:
    got = P.normalize(src)
    check(got == want, f"{src!r} -> {got!r} (want {want!r})")

print("\n[3] GOOD-PATH NO-OP")
for src in NOOP:
    got = P.normalize(src)
    check(got == src, f"{src!r} unchanged -> {got!r}")

print("\n[4] IDEMPOTENCY  f(f(x)) == f(x)")
for src, _ in REPAIR + STRIP + BOLD:
    once = P.normalize(src)
    twice = P.normalize(once)
    check(once == twice, f"{src!r}: {once!r} == {twice!r}")

print("\n" + "=" * 66)
print(f"  {_PASS} passed, {_FAIL} failed")
print("=" * 66)
# Discovery runs the checks above at import; this turns their tally into a real
# verdict. Kept as a script too — `.venv/bin/python tests/test_paralinguistics.py` still exits
# non-zero on failure, which is how this file has always been used.
class ParalinguisticMatrix(unittest.TestCase):
    def test_every_check_passed(self):
        self.assertEqual(_FAIL, 0,
                         f"{_FAIL} of {_PASS + _FAIL} checks failed — detail printed above")


if __name__ == "__main__":
    raise SystemExit(1 if _FAIL else 0)
