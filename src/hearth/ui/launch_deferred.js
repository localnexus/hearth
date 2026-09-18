// launch_deferred.js — deferred start: a Start refused by someone else's
// maintenance lock is held page-side and re-issued once the lock frees, rather
// than making the operator watch the clock and press again themselves.
//
// Three pieces. The amber line that counts the wait out loud (deferredMessage,
// re-said every second). The card's own submit adapter — the one place this
// page sees Start's answer, and where a second press while armed is a cancel
// and never a second session. And checkDeferredStart, which the state poll
// calls every pass, and which gives up only when the compaction being waited
// on failed outright.
//
// Spliced into the launch page's own <script> block, so the scope above is the
// host: api() from ui/admin_shell.js, the switch card as `card` (the wait
// speaks beside the Start button, never on the page's status line), and
// refresh(). The page's card mount reads deferredSubmit by name, and the poll
// reads `deferred` to know whether a finished Start line has gone stale.
let deferred = null;       // an armed start, held behind someone else's maintenance lock
let deferredTimer = null;  // the 1s ticker that keeps the amber line counting
let reissuing = false;     // a re-issue POST is in flight — polls must not double-fire it

// ── deferred start: a Start refused by someone else's maintenance lock is
// held page-side and re-issued once the lock frees, rather than making the
// operator watch the clock and press again themselves. ──────────────────────

function deferredMessage() {
  const holder = deferred.holder;
  const who = holder.character;
  const secs = Math.round((Date.now() - deferred.since) / 1000);
  const what = holder.op === "compact"
    ? "a compaction of " + who + " is running (~2 min)"
    : "maintenance on " + who + " is running";
  return what + " — your session starts when it finishes · waiting " + secs + " s";
}

function armDeferred(body, holder) {
  deferred = { body, since: (deferred && deferred.since) || Date.now(), holder };
  deferredTimer = setInterval(() => card.say(deferredMessage(), "warn"), 1000);
}

function disarmDeferred() {
  if (deferredTimer) clearInterval(deferredTimer);
  deferredTimer = null;
  deferred = null;
}

// The card's own `submit` adapter — the only place the page sees Start's
// answer. A second press while armed is a cancel, never a second session.
async function deferredSubmit(body) {
  if (deferred) {
    disarmDeferred();
    return { status: 409, data: { ok: false, errors: ["deferred start cancelled"],
                                   say: "deferred start cancelled", tone: "" } };
  }
  const r = await api("/admin/switch", { json: body });
  if (r.status === 409 && r.data && r.data.maintenance) {
    armDeferred(body, r.data.maintenance[0]);
    r.data.say = deferredMessage();
    r.data.tone = "warn";
  }
  return r;
}

// Called every poll. Re-issues the held Start once the lock that refused it
// frees, unless the compaction it was waiting on failed outright.
async function checkDeferredStart(st) {
  if (!deferred) return;
  const holder = deferred.holder;
  const cq = (st.data && st.data.compact_queue) || [];
  const failed = cq.some((e) => e.state === "failed" && e.character === holder.character &&
                                 (!holder.session || e.session === holder.session));
  if (failed) {
    disarmDeferred();
    card.say("compaction failed — the session was not started; press Start to try again", "err");
    return;
  }
  const maint = (st.data && st.data.maintenance) || [];
  if (maint.some((m) => m.op !== "session")) return;
  if (reissuing) return;
  reissuing = true;
  if (deferredTimer) clearInterval(deferredTimer);
  deferredTimer = null;
  const body = deferred.body;
  try {
    const r = await api("/admin/switch", { json: body });
    if (r.data && r.data.ok) {
      deferred = null;
      card.say("session starting", "ok");
      refresh();
    } else if (r.status === 409 && r.data && r.data.maintenance) {
      armDeferred(body, r.data.maintenance[0]);
    } else {
      deferred = null;
      card.say("start failed: " + ((r.data && r.data.errors && r.data.errors.join(" · ")) || r.status), "err");
    }
  } catch (e) {
    deferred = null;
    card.say("start failed: " + e.message, "err");
  } finally {
    reissuing = false;
  }
}
