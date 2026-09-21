# UX principles — what the page redesign is checked against

*The settled rules Hearth's seven pages are judged by: a rule, a source a reader can open, one named place
where it holds or breaks today, and what the redesign inherits. The placement map, the wireframes and the
build strokes are each checked against these rows — a later step that contradicts one argues it here first,
not in the markup. Row numbers are the control inventory's (268 controls across launch, panel, settings,
roster, memory, first-run and pairing); source keys are expanded under [Sources](#sources).*

| # | The rule | Source | Where Hearth meets or breaks it today | What the map inherits |
|---|---|---|---|---|
| 1 | A page states what is actually happening, continuously, rather than remembering what it last did. | Nielsen #1 (visibility of system status); Shneiderman #3 | **Meets** on launch: the state line is re-polled every few seconds, so a conversation started at the desk appears on its own and one stopped there drops off. **Breaks** at launch · Externals › Run (row 37) — the press holds until the command finishes, the wait its only signal. | Pages show live process state, not the result of their own last press; anything that can outlast a second shows elapsed progress. |
| 2 | Labels, previews and refusals speak the task's language; request shapes, JSON and absolute paths belong in the log. | Nielsen #2 (match with the real world) | **Breaks** three times: the resting page at `/` prints `POST /admin/bot/start {"mode": "new"}` as body text (`supervisor/routes/proxy.py`, `_OFFLINE_PAGE`); the forget preview ends with `repeat with "yes": true` (`supervisor/curation.py`) right above memory row 249, a button whose whole job is that second press; the device-forget preview adds `press Confirm (or repost with {"yes": true})` and the machine's absolute archive path (`supervisor/routes/devices.py`). | No request line, literal body or absolute path on a surface a reader presses; a preview names what changes in the reader's terms. |
| 3 | A destructive or persistent action previews, writes on a second explicit confirm, and leaves one recoverable generation behind. | Nielsen #3 (control and freedom); Shneiderman #6 | **Meets** — the strongest pattern in the pages today: settings rows 88–89 (Apply / Cancel behind their own confirm panel), memory rows 248–250, and four roster buttons labelled *Preview (writes nothing)* (rows 217, 224, 231, 239). One documented exception: the first-run model id (rows 253–254), where the value is the server's own listing. | Preview-then-confirm is the default shape for every writing control the redesign moves or adds; a new exception is argued the way the model id was. |
| 4 | The same kind of thing looks the same on every page, so a reader learns the surface once. | Nielsen #4 (consistency); Shneiderman #1 | **Breaks.** Five of the seven pages build from `class="card"` and open with the same unlock panel; the control panel uses a different vocabulary end to end (`kpanel`: `kp-char`, `kp-voice`, `kp-listen`, `kp-switch`) with no card element at all; pairing has no container wrapper of any kind. | One container vocabulary, one unlock pattern, one heading rhythm, used by all pages including the panel — or a recorded reason why the panel differs. |
| 5 | Controls that cannot succeed now are absent, not present-and-rejecting; values are checked before anything is written. | Nielsen #5 (error prevention); Norman (constraints) | **Meets** on launch: the conversation picker, memory picker and mic-off box (rows 11–13) are start-only and vanish once a conversation runs, rather than offering a change that would be refused. **Breaks** on settings: the 21 `model.weights.*` rows (97–117) render as editable inputs although that table is written by enrolment and never by hand, and nothing server-side refuses a hand-write. | A control's presence is a claim that it can be used now; read-only facts render as facts, not inputs. |
| 6 | The reader picks from what is on screen rather than holding a value in their head — above all on a handset. | Nielsen #6 (recognition over recall); Shneiderman #8 | Both sides visible: the access-key field (rows 1, 81, 203, 241, 251) asks for 64 hex characters on five pages, which no one should type on a phone; pairing (rows 267–268) is the six-digit answer, and pre-fills the device-name field with a guess. | Every phone path reaches its page through pairing, never through typing the key; where a reader must supply a value, the likely one is already filled in. |
| 7 | When a page will not do something, it says what will, and where. | Nielsen #9 (recognise, diagnose, recover) | **Meets.** Settings refuses three things and points each at a real surface — who is live to the switch card, live knobs and presets to the panel, secret values nowhere. First run refuses to move the model server address and prints the command that does; memory refuses the heavy rebuild and names its command. | Keep the shape — refusal, reason, destination — and make the destination a link wherever one exists. |
| 8 | Anything carrying the signifiers of a control must respond; signifier and affordance agree, or the reader learns to distrust the page. | Norman (affordances and signifiers) | **Breaks** on roster, where two lists sit in the same shape: the companion list (row 209) is plain text with no click listener, while the branch card's record row (row 235) is clickable and fills the juncture field. Identical-looking, one inert. | Lists are either interactive rows throughout or plainly typeset as a summary; no middle state. |
| 9 | Placement follows what a setting governs, not how often someone reaches for it. | Norman (mapping, conceptual models); the layer/owner columns in `settings_registry/` | **Partly meets**, by the ownership split the manual teaches: the panel owns the live override layer (rows 56–70) and settings refuses to touch it; identity-scope voice fields live on roster. **Breaks as duplication** — the four selection controls appear three times: rows 5–8, 72–75, 261–264. | The placement rule restated below, plus: one control per setting, placed once; a second appearance is a link to the first, not a copy. |
| 10 | Time and error rate rise with the number of visible options; show the commonly used few and defer the rest behind an explicit step. | Hick's law (Hick 1952); Nielsen on progressive disclosure | **Breaks** hardest on settings: 122 rows, 113 of them generated field editors in one flat per-file form (rows 90–202) — 21 being the weights facts from rule 5 that no reader sets. **Meets** on launch, where whole cards appear only under their condition. | Settings is grouped and staged, not flattened; rare and derived fields sit behind a disclosure; controls visible at rest is a count the map states per page. |
| 11 | Related controls are enclosed together and shown or hidden as one; grouping carries meaning by proximity and enclosure before any label does. | Gestalt proximity and enclosure; Nielsen on progressive disclosure | **Meets** on launch, settings, roster, memory and first run, where each card names its own condition. **Breaks** on the panel, which has no enclosure vocabulary (rule 4), and on pairing, which has none either. | Every control belongs to exactly one named group; visibility rules attach to groups, never to lone controls. |
| 12 | Pointing time falls with target size and rises with distance; a handset control needs a physical target near a centimetre square, and press-and-hold is a poor phone idiom. | Fitts's law (Fitts 1954, with the NN/g summary); NN/g on touch-target size; platform guidance from Apple and Material Design 3 | **Breaks** everywhere: no page style sets a minimum height on any control — `min-height` appears only on message lines and one textarea. The panel's push-to-talk control (row 47) is a sustained press, and its rail, compose row and sliders were sized for a desk pointer. | A stated minimum target size and spacing every page meets; momentary controls get a latched alternative; sliders get a typed or stepped one on narrow screens. |
| 13 | The narrow, one-column case is designed first; the desk is the widening of it, not the reverse. | Responsive and adaptive layout guidance from Apple and Material Design 3 | **Mostly meets**: all seven pages and the talk page carry a `width=device-width` viewport declaration and set columns in `em`. **Breaks** at the resting page at `/` — no viewport declaration at all, and `max-width: 34em; margin: 4em auto` inline, on the page a reader is likeliest to meet first from a phone. | Every page, the resting page included, is laid out at handset width first and works at the desk by widening; none is exempt for being small. |
| 14 | A sequence of steps has a visible end state, so the reader knows they are done rather than guessing. | Shneiderman #4 (dialogs yield closure) | **Meets** on first run: three numbered steps — your model server, bring the companion up, say something — ending in a done panel shown once the companion's turn count passes zero. | Any multi-step flow the redesign introduces (pairing, first conversation, device switching) ends in a stated, visible finish, not in a page that merely stops changing. |

---

## What the placement map inherits

**The placement rule, as a consequence rather than a decree.** *A setting lives on the page for the scope it
applies to; frequency of reach governs position within a page only.* The first half follows from rule 9 —
placement mirrors the ownership and layer the settings registry already records — reinforced by rule 4, since
a reader who learns one scope-to-page mapping predicts the rest. The second half follows from rules 10 and
12: frequency is a cost *inside* a page (thumb travel, options passed on the way), which is what Fitts and
Hick price; it is not evidence about which page a setting belongs to. Concretely: place-layer settings on the
settings pages, identity-layer settings on the character profile page, the live override layer and session
keys on the live panel.

**One tension recorded, not resolved here.** The four selection fields are place-layer and operator-owned,
which the rule alone would send to the settings pages; the manual's reason for the switch card is that
applying a selection is a verb acting live, not a value being written. Rule 9 and the rule's wording pull
opposite ways on rows 5–8, 72–75 and 261–264. The map settles this explicitly — most likely "placed once,
linked from the other two" — rather than inheriting three copies by default.

**The constraints the owner's stated intent imposes.**

1. **The microphone opens inside the tap that starts.** A browser grants microphone access only from within a
   user gesture, so Start requests the microphone in the same press that brings the conversation up, and Stop
   tears it down. The separate talk page cannot survive as a second hop — its own text tells the reader to
   start on the launch page first and only then press. Its controls fold into the launch flow.
2. **The conversation survives a dark screen.** A hidden tab is discarded by the phone's browser, so nothing
   keeping a live conversation alive may depend on a page's script staying resident.
3. **Devices are chosen, and changeable mid-sitting.** A picker offering headset, earpiece and USB-C first,
   with the phone microphone and media speaker as the fallback, sits with the start controls rather than on a
   page of its own, and can be changed while a conversation runs.
4. **Two places, one page each.** Every page works on a phone browser over the private network and at the
   desk — which makes rules 12 and 13 binding rather than aspirational, and forbids any phone path requiring
   the access key to be typed (rule 6).

**The tension to watch while placing.** The fold, the device picker and the mid-sitting switch all land on
the launch page, which already carries the largest control count of the seven after settings. Rules 10 and 11
apply to the result: the map states the launch page's visible-at-rest control count with the folded controls
included, and groups them, rather than counting the fold as free.

---

## Sources

- **Nielsen's ten heuristics** (the numbering above is this article's) — Jakob Nielsen, *10 Usability
  Heuristics for User Interface Design*, Nielsen Norman Group, 1994, last reviewed 2024:
  <https://www.nngroup.com/articles/ten-usability-heuristics/>; #1 in depth, Aurora Harley, 2018:
  <https://www.nngroup.com/articles/visibility-system-status/>.
- **Shneiderman's eight golden rules** — Shneiderman, Plaisant, Cohen, Jacobs and Elmqvist, *Designing the
  User Interface*, sixth edition, Pearson, 2016, section 3.3.4; author's summary:
  <https://www.cs.umd.edu/~ben/goldenrules.html>.
- **Norman** — Donald A. Norman, *The Design of Everyday Things*, revised and expanded edition, Basic Books,
  2013 (affordances, signifiers, mapping, constraints, conceptual models).
- **Gestalt grouping** — Aurora Harley, *Proximity Principle in Visual Design*, NN/g, 2020:
  <https://www.nngroup.com/articles/gestalt-proximity/>.
- **Progressive disclosure** — Jakob Nielsen, NN/g, 2006:
  <https://www.nngroup.com/articles/progressive-disclosure/>.
- **Fitts's law** — Paul M. Fitts, *The information capacity of the human motor system in controlling the
  amplitude of movement*, Journal of Experimental Psychology 47(6), 1954, pp. 381–391; applied summary,
  Raluca Budiu, NN/g, 2022: <https://www.nngroup.com/articles/fitts-law/>.
- **Hick's law** — W. E. Hick, *On the rate of gain of information*, Quarterly Journal of Experimental
  Psychology 4(1), 1952, pp. 11–26. No vendor page was verified for this one, so the paper is named instead.
- **Touch-target size** — Aurora Harley, *Touch Targets on Touchscreens*, NN/g, 2019:
  <https://www.nngroup.com/articles/touch-target-size/> — about 1 cm × 1 cm physical, minimum.
- **Platform guidance** — Apple, *Human Interface Guidelines*:
  <https://developer.apple.com/design/human-interface-guidelines>; Google, *Material Design 3*:
  <https://m3.material.io/>. Both landing pages were checked and resolve; their deep pages render in the
  browser and were not quoted, so no figure above is attributed to either.
- **Hearth instances** — row numbers are the control inventory's; file references are paths in this
  repository; layer and owner come from `src/hearth/config/settings_registry/`.
