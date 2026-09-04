"""supervisor/roster/ — /admin/roster: the character-roster wizard behind the door.

The onboarding half of the roster-management arc (facade-hosted per the
write-layer rule signed (c) 2026-09-02: :65000 displays the roster and links
over; every operator-layer write lives HERE, behind the bearer). The wizard
mechanizes the users-manual's six onboarding steps along the design's signed
split — invisible everything mechanical, visible only what a person actually
decides:

  MECHANIZED: directory scaffolding · clip conditioning (ffmpeg → mono
  24 kHz s16 when available; a conforming WAV is accepted as-is without it) ·
  voice.toml generation (registry-shaped: the VoiceFile schema is the form
  contract) · VOICE-SOURCE.md provenance generation from the same answers
  (one entry of truth, two files written) · the loader-verification probe
  (config_loader.load_voice + compose_persona — the exact startup path) ·
  the [memory.companions] tier entry.

  KEPT VISIBLE: the name · the persona text (## IDENTITY + ## SOUL) · the
  sample + its license/source attestation (provenance REQUIRES a human
  answer) · the memory tier. Audition/promotion stays a human step — the
  manual's rule stands: your ear decides. The wizard ends by handing off to
  /admin/launch; it grows no restart button of its own.

Preview-then-confirm, stateless: without ``yes`` the SAME multipart request
runs every validation (clip conditioned in scratch and discarded) and answers
a report; nothing persists. Confirmed, the wizard is CREATE-ONLY — an
existing character (either root) answers 409; editing a living persona is a
different, later surface. A failed verification rolls the new directory back.

memory.toml is edited by targeted line insertion under [memory.companions]
(comments preserved; parse-verified before the atomic replace) — and the
response says plainly that enrollment lands at the next process start (the
effect-time audit: nothing under [memory] is hot).

The EDITING half (the roster arc's second stroke) lives here too, same
door, same preview-then-confirm discipline:

  PERSONA EDITOR — read + rewrite an existing character's persona.md (or a
  persona.<variant>.md sibling, including creating a NEW variant). Writes
  always land in the DATA overlay: editing a character whose persona resolves
  to the shipped root copies-on-write into DATA (the lookup rule then shadows
  it), and the shipped tree is never touched. An overwrite keeps one backup
  generation (<file>.prev, reported); the write is verified with
  compose_persona — the exact startup path — and rolled back if composition
  breaks. Effect time stated honestly: composition happens at bot start /
  live-switch prepare, never mid-sitting.

  ADD-A-VOICE — the wizard's clip pipeline pointed at an EXISTING character:
  per-TAG create-only (an existing bundle is refused, never overwritten),
  written under DATA (voice_dir's per-voice lookup means this works for
  shipped characters without copying their persona), provenance appended to
  the character's DATA-side VOICE-SOURCE.md from the same one set of answers.
  The new tag appears in the switch pickers immediately (choices() reads the
  disk at call time); audition stays yours — your ear decides.

  BRANCH (fork) — the memory CLI's fork verb (hearth/memory/fork.py) behind
  the same door: a new character whose records hold the source's history up
  to a juncture (docs/memory.md, "Forking the track at a juncture"). The
  route is a thin JSON skin over the CLI's own plan/execute pair — identical
  validation, selection, rollback. ONE deliberate divergence, curation.py's
  posture verbatim: the backend REPLAY stays at the desk (extraction over
  every record is minutes, unbounded by request timeouts) — a non-floor fork
  answers "created" plus the exact rebuild command to run.

API (mounted iff [serve.supervisor] enabled):
    GET  /admin/roster         → the wizard page (static contentless shell,
                                 auth-exempt like /admin/launch; data authed)
    GET  /admin/roster/state   → roster listing: names, voices, personas,
                                 memory backend map, active selection, ffmpeg
    POST /admin/roster/onboard → multipart form; "yes" absent = dry-run report
    GET  /admin/roster/persona?character=<c>&persona=<v> → the persona text
    POST /admin/roster/persona → JSON {character, persona?, text, yes?}
    POST /admin/roster/voice   → multipart form; "yes" absent = dry-run report
    POST /admin/roster/fork    → JSON {character, as, until, include_sessions?,
                                 yes?}; yes absent = the full plan, nothing written

── the package layout ───────────────────────────────────────────────────────
One module per verb, over two the verbs share; imports run strictly downward
this list, and roster_page.html sits beside page.py:

    bundle.py   the clip pipeline (probe → condition) and the two files
                written beside every sample: voice.toml + VOICE-SOURCE.md
    forms.py    every refusal: dir-safe names, the persona contract checked
                on SUBMITTED text, provenance, the known tiers
    page.py     the read side — _PAGE (markup + the three ui/ sections),
                the shell route, and /state
    onboard.py  the create-a-character transaction, its dry run, the route
    persona.py  the persona editor (GET the text, POST a verified write)
    voices.py   add-a-voice to an existing character
    branch.py   the fork route: plan → preview → execute

This __init__ is the façade: it re-exports every name the parts define, so
``from hearth.supervisor import roster`` still reaches all of them (routes.py
takes add_routes; the page tests take _PAGE; nothing else reaches in).
"""

from __future__ import annotations

from aiohttp import web

# Listed in dependency order, the order the layout above reads in: a part only
# ever imports one above it, and tests/test_package_facades.py holds that line.
from .bundle import (
    _FFMPEG_TIMEOUT_S, _MAX_CLIP_S, _MIN_CLIP_S, _VOICE_SOURCE_ADD,
    _VOICE_SOURCE_MD, _VOICE_TOML, _check_duration, _condition_clip,
    _probe_wav, ffmpeg_path)
from .forms import (
    _TIERS, _check_fields, _check_voice_fields, _known_characters,
    _validate_persona_text)
from .page import _PAGE, _page, _state
from .onboard import _dry_run, _onboard, _onboard_route
from .persona import (
    _MAX_PERSONA_CHARS, _persona_get, _persona_names, _persona_post,
    _persona_write)
from .voices import _add_voice, _voice_route
from .branch import _fork_preview, _fork_route

#: The re-exported surface. The underscored names go out too, deliberately:
#: this package was one module until the split, and the tests reach for its
#: internals by name (_PAGE, _condition_clip, _known_characters …). Exporting
#: only the two names routes.py calls would have made a move into a breaking
#: change.
__all__ = ["add_routes", "ffmpeg_path"]


def add_routes(app: web.Application) -> None:
    """Called by routes.build_mount — same door, same middleware."""
    app.router.add_get("/admin/roster", _page)
    app.router.add_get("/admin/roster/state", _state)
    app.router.add_post("/admin/roster/onboard", _onboard_route)
    app.router.add_get("/admin/roster/persona", _persona_get)
    app.router.add_post("/admin/roster/persona", _persona_post)
    app.router.add_post("/admin/roster/voice", _voice_route)
    app.router.add_post("/admin/roster/fork", _fork_route)
