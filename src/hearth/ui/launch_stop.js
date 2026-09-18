// launch_stop.js — the launch page's "Running" card: what stopping would do to
// this conversation, and the words for it.
//
// Four pure helpers lead, each cut out of the SERVED page and run under node
// (test_stop_card.py, test_voice_page.py): stopButtonLabel, the button's own
// words; defaultLabel, the name a kept conversation is offered; stopCardSeed,
// what the card shows before the first turn exists — the working row once there
// is one, the sitting's own start switches before that; and routeLine, which
// tells a desk headset's silence apart from a remote device's countdown,
// because the same word for both would be true and useless.
//
// Then the countdown between polls: the poll's number is remembered with the
// moment it arrived and the seconds shown are derived from the clock, so the
// line ticks instead of sitting still and jumping — and it never runs
// backwards, since a later poll is adopted only when it is lower.
//
// Then the two writes. postRetain tells the daemon the keep/delete choice as it
// is made, so a stop that never happens still leaves the right intent on file.
// doStop counts the seconds out loud because the memory close tail owns a real
// budget ([memory] close_budget_s) before the supervisor's grace even starts,
// and a static "stopping…" over two minutes reads as a dead button.
//
// Spliced into the launch page's own <script> block, so the scope above is the
// host: $, show and api from ui/admin_shell.js, sayAt(), the switch card as
// `card`, the shared `busy` and `devices`, and refresh(). The poll calls
// drawRoute() and updateStopCard(); the keep/name wiring at the foot of this
// file runs where it always ran, in the page's wiring section.
let stopWorkingId = null;  // the working file currently shown on the Stop card
let stopTouched = false;   // the person's own edit outranks the next poll, until the working id changes

// The Stop button's own words, and the Stop card's suggested name — both cut
// out and run under node by test_stop_card.py, so both stay pure (no DOM, no
// clock of their own).
function stopButtonLabel(keep, label) { return keep ? 'Stop and keep "' + label + '"' : "Stop and delete"; }
function defaultLabel(character, when) {
  const p2 = (n) => String(n).padStart(2, "0");
  return character + " · " + when.getFullYear() + "-" + p2(when.getMonth() + 1) + "-" +
         p2(when.getDate()) + " " + p2(when.getHours()) + ":" + p2(when.getMinutes());
}
// What the Stop card should show: the working row once it exists, and before
// the first turn the start-time switches the state already carries (`switches`
// null when there is nothing to seed from). `fallback` is the clock-made
// default label, passed in so this stays pure too.
// A countdown, as a person reads one: 160 → "2:40". Pure, and clock-free —
// the seconds are worked out by the caller and handed in.
//
// `devices` is the enrolled list, so `remote:pixel-3f4a` reads back as the name
// its owner gave it. An id nobody has a row for is its own label — which is
// what a conversation started before the device was forgotten looks like.
function graceText(seconds) {
  const whole = Math.max(0, Math.round(Number(seconds) || 0));
  return Math.floor(whole / 60) + ":" + String(whole % 60).padStart(2, "0");
}

// What the Stop card says about where this sitting's audio is. Pure, and cut
// out by test_voice_page.py: `switches.route` is the word fixed at Start,
// `route` is what the running sitting reports about it (null before the first
// poll answers, or when the companion predates the route), and `graceLeft` is
// the countdown in seconds, derived from the clock by the caller so this stays
// clock-free.
//
// The two losses are DIFFERENT stories and the card has to tell them apart. A
// desk headset that is away is silence you can wait out: the conversation
// waits for it indefinitely, and only that device will do. A device on the far
// end of a socket is a countdown, at the end of which the conversation closes
// itself. The same word "waiting" for both would be true and useless.
function deviceLabel(devices, id) {
  for (const device of devices || []) {
    if (device && device.id === id) return device.label || id;
  }
  return id;
}

function routeLine(switches, route, graceLeft, devices) {
  const word = (switches && switches.route) || "desk";
  const where = word === "desk" ? "the desk"
              : deviceLabel(devices, word.replace(/^remote:/, ""));
  let line = "audio: " + where;
  if (route && route.kind === "desk") {
    if (route.state === "lost")
      line += " — the headset is away; waiting, and it will come back on the " +
              "same device only";
    else if (route.state === "recovered") line += " — the headset came back";
    else if (route.state === "unpinned") line += " — no device pinned";
  } else if (route && route.kind === "remote") {
    if (route.state === "waiting")
      line += " — waiting for it to connect (open the talk page on it)";
    else if (route.state === "lost") {
      const left = graceLeft == null ? null : graceText(graceLeft);
      line += " — waiting for " + where + (left ? ", " + left + " left" : "") +
              "; then this conversation closes";
    }
    else if (route.state === "ended") line += " — it did not come back; closing";
    else if (route.state === "connected") {
      line += " — connected";
      if (route.path) line += " (" + route.path + ", " + (route.buffer_ms || 0) + " ms buffer)";
      if (route.shed_ms) line += "; the phone is falling behind (" + route.shed_ms + " ms dropped)";
    }
  }
  return line;
}

function stopCardSeed(row, switches, fallback) {
  if (row) {
    const keep = row.retain === true;
    return { keep: keep, name: keep ? ((row.title || row.name) || fallback) : "" };
  }
  const sw = switches || {};
  const keep = sw.retain === true;
  return { keep: keep, name: keep ? (sw.keep_name || fallback) : "" };
}

// ── the countdown between polls ─────────────────────────────────────────────
// The page polls on its own cadence, so a countdown drawn only on arrival
// would sit still for seconds at a time and then jump — which reads as a
// stuck page at exactly the moment a person is watching it closely. So the
// poll's number is remembered with the moment it arrived, and the seconds
// shown are derived from the clock, the way the stopping line ticks.
//
// It never runs backwards: a later poll is adopted only when it is LOWER than
// what is already on screen, so a slow answer cannot push the count back up.
let graceLeft = null, graceAt = 0, graceTimer = null, graceBot = null;

function graceNow() {
  if (graceLeft == null) return null;
  return Math.max(0, graceLeft - Math.floor((Date.now() - graceAt) / 1000));
}

function noteGrace(route) {
  const counting = route && route.kind === "remote" && route.state === "lost" &&
                   route.grace_left != null;
  if (!counting) { graceLeft = null; return; }
  const showing = graceNow();
  if (showing === null || route.grace_left < showing) {
    graceLeft = route.grace_left;
    graceAt = Date.now();
  }
}

function drawRoute(bot) {
  graceBot = bot;
  noteGrace(bot.route);
  $("routeline").textContent = routeLine(bot.switches, bot.route, graceNow(),
                                        devices);
  if (graceLeft != null && graceTimer === null) {
    graceTimer = setInterval(() => {
      if (graceLeft == null || !graceBot) {
        clearInterval(graceTimer); graceTimer = null; return;
      }
      $("routeline").textContent =
        routeLine(graceBot.switches, graceBot.route, graceNow(), devices);
    }, 1000);
  } else if (graceLeft == null && graceTimer !== null) {
    clearInterval(graceTimer); graceTimer = null;
  }
}

// The Stop card's keep switch and name follow the working file — the newest
// unheld session on the shelf, which exists only from the first turn onward.
// Before it appears the card is seeded instead from the sitting's own start
// switches (`bot.switches`, already in the polled state): a sitting started
// with keep says "Stop and keep …" from the moment it is up. A `touched` flag
// keeps the person's own edit from being overwritten by the next poll; it is
// keyed on the sitting — the working id while there is a working file, and
// "boot:<pid>" before that — so a fresh sitting resets it either way, and the
// working row takes over unchanged once it lands.
async function updateStopCard(character, bot) {
  let row = null;
  try {
    const r = await api("/admin/sessions?character=" + encodeURIComponent(character || ""));
    row = ((r.data && r.data.sessions) || []).find((m) => m.held === false) || null;
  } catch { /* the shelf is a convenience; the card still works without it */ }
  const booting = !row && !!bot && (bot.state === "starting" || bot.state === "running");
  const sittingId = row ? row.session_id : (booting ? "boot:" + bot.pid : null);
  if (sittingId && sittingId !== stopWorkingId) {
    stopWorkingId = sittingId;
    stopTouched = false;
  }
  if (!stopTouched) {
    const seed = stopCardSeed(row, booting ? bot.switches : null,
                              defaultLabel(character, new Date()));
    $("stopkeep").checked = seed.keep;
    $("stopname").disabled = !seed.keep;
    $("stopname").value = seed.name;
    $("stopbtn").textContent = stopButtonLabel(seed.keep, seed.name);
  }
  if (row && row.forked_from) {
    $("forkline").textContent = 'Continuing "' + (row.title || row.forked_from) +
      '": keeping replaces it, deleting leaves it untouched.';
    show("forkline", true);
  } else {
    show("forkline", false);
  }
}

async function postRetain() {
  const checked = $("stopkeep").checked;
  const name = $("stopname").value.trim();
  try {
    const r = await api("/admin/bot/retain", { json: { retain: checked, name: name || undefined } });
    if (r.status === 409) { sayAt("stopstate", "nothing is running", ""); return; }
    sayAt("stopstate", checked ? "will be kept" : "will be deleted", "");
  } catch (e) {
    sayAt("stopstate", e.message === "401" ? "key refused" : "failed: " + e.message, "err");
  }
}

// A stop that is WORKING can sit here for two minutes: the graceful ladder runs
// the memory close tail first, and that owns a real budget ([memory]
// close_budget_s, 120 s by default) before the supervisor's grace even starts.
// A single static "stopping…" over that span reads as a dead button — observed
// 2026-09-09, where the operator clicked again and fired a second SIGINT at an
// already-dying bot. So: count the seconds out loud, say how long is normal,
// and hold the button down until the answer lands.
async function doStop() {
  const keep = $("stopkeep").checked;
  const name = $("stopname").value.trim();
  const body = { retain: keep };
  if (keep && name) body.name = name;
  busy = true;
  $("stopbtn").disabled = true;
  const t0 = Date.now();
  const elapsed = () => sayAt("stopstate", "stopping… " + Math.round((Date.now() - t0) / 1000) +
                               "s · the close runs first (up to ~2 min)", "warn");
  elapsed();
  const tick = setInterval(elapsed, 1000);
  try {
    const r = await api("/admin/bot/stop", { json: body });
    clearInterval(tick);
    if (r.data && r.data.ok) {
      sayAt("stopstate", keep ? 'stopped — kept as "' + name + '"' : "stopped — conversation deleted", "ok");
    }
    else sayAt("stopstate", "stop failed: " + JSON.stringify((r.data && r.data.error) || r.status), "err");
  } catch (e) {
    clearInterval(tick);
    sayAt("stopstate", e.message === "401" ? "key refused" : "stop failed: " + e.message, "err");
  } finally { clearInterval(tick); $("stopbtn").disabled = false; busy = false; refresh(); }
}

$("stopkeep").addEventListener("change", () => {
  stopTouched = true;
  const checked = $("stopkeep").checked;
  $("stopname").disabled = !checked;
  if (checked && !$("stopname").value.trim()) {
    $("stopname").value = defaultLabel(card.character(), new Date());
  }
  $("stopbtn").textContent = stopButtonLabel(checked, $("stopname").value);
  postRetain();
});
$("stopname").addEventListener("input", () => {
  stopTouched = true;
  $("stopbtn").textContent = stopButtonLabel($("stopkeep").checked, $("stopname").value);
});
$("stopname").addEventListener("change", postRetain);
$("stopname").addEventListener("blur", postRetain);
$("stopbtn").addEventListener("click", doStop);
