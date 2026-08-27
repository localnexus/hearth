"""paralinguistics.py — deterministic repair of malformed paralinguistic cue tags.

Normalizes an enclosed *bare* cue root — inside `*…*`, `(…)`, `[…]`, or `{…}` — to
the one canonical Chatterbox-Turbo tag, tolerating case, padding, and morphology
(-s / -es / -ing / -ed, silent-e aware). It converts an enclosure ONLY when its
trimmed content is exactly a variant of a single canonical cue root (or the
`clear throat` family) and nothing else.

One narrow exception to the bare-root rule: a short CURATED list of multi-word
affect phrases the model is actually observed to emit as fumbled tags (e.g.
`[soft sigh]` → `[sigh]`), each reducing cleanly to one canonical cue. See
`_PHRASES`. This is a whitelist, not a general modifier rule.

It also STRIPS (the one destructive op) every square-bracketed OR brace-enclosed
token that is NOT one of the canonical cues (9 shipped breath cues + 10 pilot
style tokens — see `_STEMS`) — a CATCH-ALL, applied after
repair. The reasoning: once repair has canonicalized everything that maps to a cue,
anything still in `[…]` or `{…}` is BY DEFINITION not a cue, and Chatterbox would
SPEAK it aloud ("softly", "gentle smile") — always a bug, never the intent. Silence
is the faithful rendering; the surrounding prosody carries the beat. A smile is
silent, a pause is punctuation, and a free-form adverb is a stage direction with no
engine channel. (An earlier revision claimed "Turbo exposes no prosody control" —
falsified: the tokenizer carries 19 trained cue/style tokens, which is what the
pilot block adopts.)

Strip covers SQUARE BRACKETS and BRACES, but NOT `*…*` / `(…)`: asterisks and parens
may be desired RP stage-direction and are left for the voice-native-action layer.
Braces get no such grace — a curly pair is never a prose convention, only a fumbled
tag syntax (some models drift to `{cue}` instead of the prompt-mandated `[cue]`),
so a SURVIVING `{…}` after repair is always a bug. Brace
strips always classify UNKNOWN (the `_STRIP` known-families are bracket-only) — that
is deliberate: the model's brace-drift is itself a novel signal worth surfacing.

Every strip is REPORTED, never logged from here — this module is PURE (stdlib `re`
only, no I/O, no logger) and stays that way, which is what makes its test matrix
exhaustive and cheap. `normalize_with_report()` returns what it removed; the caller
(mlx_tts_service.run_tts) owns the side effect. Strips are classified `known` (an
already-adjudicated family — see `_STRIP_HEADS`) vs UNKNOWN (novel). The unknowns
are the point: they are the discovery signal — a tag the model reached for that we
don't support yet (a `[softly]` tag), surfaced without either of us having to
predict it. A catch-all that stripped silently would swallow exactly that signal.

One more marker family IS destructively handled: MARKDOWN BOLD. `**…**` /
`***…***` marker pairs are stripped — markers only, the words stay — plus any
orphaned `**`-run. This is the "emphasis on real words → strip markers, keep words"
case: doubled asterisks were field-heard as an audible artifact (bolded list items),
where single asterisks are inert. Bold-strip runs BEFORE repair, so `**sigh**`
degrades to the prose word instead of leaving stray `*` around a repaired tag.
Single `*…*` remains untouched here by design — that adjudication (remap-to-cue vs
strip vs keep) is the full handler's job, not this slice's.

It deliberately does NOT: translate open-ended multi-word prose stage-directions
(`*sighs heavily*`, `*pulls you into a hug*` — a second, non-whitelisted word
crosses the modifier threshold), STRIP `*…*` / `(…)` enclosures (repair still fires
there; only the catch-all strip skips them), or touch unwrapped words (`She
sighed.`). Idempotent, zero latency.

Source of truth for the nine shipped cues: the active model's
system-prompt-template.md.
"""
import re

# Canonical single-word cues → stem root(s). `clear throat` handled separately.
_STEMS = {
    "laugh":   ["laugh"],
    "chuckle": ["chuckle"],
    "sigh":    ["sigh"],
    "gasp":    ["gasp"],
    "groan":   ["groan", "moan", "murmur"],  # `moan`*/`murmur`* aliased → [groan] (murmur = pilot)
    "sniff":   ["sniff", "sniffle"],
    "cough":   ["cough"],
    "shush":   ["shush"],
    # ── pilot: the 10 DORMANT trained cue/style tokens, unstripped for ear-testing.
    # Turbo's tokenizer carries 19 trained single-token cue entries; 9 are
    # whitelisted as breath cues and the catch-all stripped these 10.
    # Canonical surface = the EXACT trained token string ([whispering], not
    # [whisper]). Deliberately NOT added to the model prompt template — the model
    # isn't invited to emit them until the ear test signs them in; this block only
    # stops the strip (and repairs morphology) so test text reaches the engine.
    "whispering":    ["whisper"],
    "angry":         ["angry"],
    "happy":         ["happy"],
    "sarcastic":     ["sarcastic"],
    "crying":        ["cry"],
    "surprised":     ["surprise"],
    "fear":          ["fear"],
    "dramatic":      ["dramatic"],
    "narration":     ["narration"],
    "advertisement": ["advertisement"],
}

# Curated multi-word affect phrases that are malformed *tag* attempts, not prose.
# These deliberately cross the "+1 word" bare-root threshold — but only for a short,
# CURATED whitelist of specific phrasings the model has actually been observed to
# emit inside brackets (e.g. an observed `[soft sigh]`). Each must reduce cleanly
# to ONE canonical cue with no loss of intent. This is NOT a general modifier rule:
# open-ended modifiers (`*sighs heavily*`) and prose stage-direction
# (`*pulls you into a hug*`) remain untouched. Extend one entry at a time, per ear.
_PHRASES = [
    (r"soft(?:ly)?\s+sigh(?:s|ing|ed)?", "[sigh]"),  # `[soft sigh]` → [sigh]
]

# The four matched enclosure pairs (asterisks, parens, brackets,
# braces). Angle brackets are intentionally excluded (mild prose ambiguity).
_ENCLOSURES = [("*", "*"), ("(", ")"), ("[", "]"), ("{", "}")]

# `clear throat` — the one multi-word canonical. `his/her/the/my` is part of the
# expression, not an extra modifier; anything beyond it fails the bare-root test.
_CLEAR_THROAT = (
    r"clear(?:s|ing|ed)?\s+(?:his|her|the|my)\s+throat"
    r"|clear(?:s|ing|ed)?\s+throat"
    r"|throat[-\s]*clear(?:ing|s|ed)?"
)


def _forms(stem: str) -> set:
    """All plausible surface forms of a stem: base, -s/-es, -ing, -ed (silent-e aware)."""
    f = {stem, stem + "s"}
    if stem.endswith(("s", "sh", "ch", "x", "z")):
        f.add(stem + "es")
    if stem.endswith("e"):
        f.add(stem[:-1] + "ing")   # chuckle -> chuckling, sniffle -> sniffling
        f.add(stem + "d")          # chuckle -> chuckled
    else:
        f.add(stem + "ing")
        f.add(stem + "ed")
    return f


def _content_alt(forms) -> str:
    # longest-first is cosmetic (the enclosure close anchors the match) but tidy.
    return "|".join(re.escape(w) for w in sorted(forms, key=len, reverse=True))


def _build():
    specs = [(f"[{tag}]", _content_alt(set().union(*[_forms(s) for s in stems])))
             for tag, stems in _STEMS.items()]
    specs.append(("[clear throat]", _CLEAR_THROAT))
    for alt, repl in _PHRASES:            # curated multi-word tag-attempts
        specs.append((repl, alt))
    rules = []
    for repl, alt in specs:
        for open_, close_ in _ENCLOSURES:
            pat = re.compile(
                re.escape(open_) + r"\s*(?:" + alt + r")\s*" + re.escape(close_),
                re.IGNORECASE,
            )
            rules.append((pat, repl))
    return rules


_RULES = _build()

# The canonical cues (9 shipped + 10 pilot), in post-repair surface form. This
# set is the KEEP-list
# for the catch-all strip below: repair runs first, so by the time we scan, every
# salvageable tag already looks exactly like one of these.
_CANONICAL = {f"[{tag}]" for tag in _STEMS} | {"[clear throat]"}

# Already-adjudicated strip families — a leading modifier stack + a silent head noun
# (`[gentle smile]`, `[dramatic pause]`). These are NOT what makes stripping happen
# any more (the catch-all does that); they exist to classify a strip as `known` so
# it doesn't drown the log. Decisions already made shouldn't compete for attention
# with novel tags — the whole value of the log is the UNKNOWNS.
_STRIP_HEADS = ["smile", "pause"]
_STRIP = [re.compile(r"\[\s*(?:\w+\s+)*" + h + r"s?\s*\]", re.IGNORECASE)
          for h in _STRIP_HEADS]

# Catch-all: any square-bracketed OR brace-enclosed token. Non-greedy by
# construction — a negated class can't span a nested delimiter, so malformed input
# can't swallow a whole line. Braces are swept alongside brackets because repair has
# already salvaged every brace cue-root (→ `[bracket]`); a SURVIVING `{…}` is a
# non-cue by definition. Unlike `*…*` / `(…)` (possible RP stage-direction, left for
# the voice-native-action layer), a brace is never prose — only a fumbled tag.
# Mismatched pairs (`{x]`, `[x}`) require a matching close, so they're left alone.
_ENCLOSED = re.compile(r"\[([^\[\]]*)\]|\{([^{}]*)\}")

# Markdown bold: a `**`(+) pair around asterisk-free content → keep the content,
# drop the markers. `\*{2,}` (not exactly 2) so `***bold-italic***` collapses in
# one pass. An orphaned `**`-run (unpaired) is never prose → becomes a space.
# Single `*` is deliberately NOT matched anywhere here (see module docstring).
_BOLD = re.compile(r"\*{2,}([^*]+)\*{2,}")
_BOLD_ORPHAN = re.compile(r"\*{2,}")


def normalize_with_report(text: str) -> tuple[str, list[dict]]:
    """normalize(), plus a report of every bracketed token stripped.

    Returns `(clean_text, strips)` where each strip is
    `{"token": "[softly]", "known": False}` — `known` marking an
    already-adjudicated family (see `_STRIP_HEADS`) vs a novel tag worth a look.

    PURE: builds the report and returns it; writing it anywhere is the caller's job.
    """
    if not text:
        return text, []
    text = _BOLD.sub(r"\1", text)          # markdown bold: markers off, words kept
    text = _BOLD_ORPHAN.sub(" ", text)     # unpaired `**`-run: never prose
    for pat, repl in _RULES:
        text = pat.sub(repl, text)

    strips: list[dict] = []

    def _sift(m: "re.Match") -> str:
        token = m.group(0)
        # group(1) = square-bracket body, group(2) = brace body (whichever matched)
        body = m.group(1) if m.group(1) is not None else m.group(2)
        inner = re.sub(r"\s+", " ", body).strip().lower()
        if f"[{inner}]" in _CANONICAL:
            return token                   # a real cue — the good path, untouched
        strips.append({
            "token": token,
            "known": any(p.fullmatch(token) for p in _STRIP),
        })
        return " "                         # replace with a space, then re-tidy

    text = _ENCLOSED.sub(_sift, text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text, strips


def normalize(text: str) -> str:
    """Repair enclosed bare cue roots to canonical tags; strip every bracketed
    non-cue and every markdown-bold marker pair (words kept). Idempotent; only
    removes broken tags and markers, never prose.

    Thin wrapper over normalize_with_report() for callers that don't want the
    report — keeps the common call site and the whole test matrix unchanged.
    """
    return normalize_with_report(text)[0]
