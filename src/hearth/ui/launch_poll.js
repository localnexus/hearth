// launch_poll.js — the launch page's state poll: one GET every four seconds,
// and every card that flips because of it.
//
// refresh() is this page's clock. It reads /admin/state once, mints the browser
// carrier cookie on the first pass (a navigation cannot carry the bearer, so
// the control-panel link would 401 without it), refreshes the Externals and
// Models cards on a slower cadence because each of those costs its own
// requests, writes the status line, and then — unless an action is in flight,
// since a card must never move under somebody's hand — flips the Companion and
// Running cards to match what came back.
//
// Three helpers travel with it. clearOnEdge (which finished messages go stale
// when the bot's state moves) and lastExitLine (why the last conversation
// ended, when the page can say at all) are pure and are cut out of the SERVED
// page and run under node by test_status_line.py and test_voice_page.py;
// needToken is the locked state, and belongs to whoever discovers it.
//
// Spliced into the launch page's own <script> block, so these are the page's
// top-level bindings and the scope above is the host: $, show, token, api and
// report from ui/admin_shell.js, sayAt(), the switch card as `card`, and the
// shared state the page declares (choices, botUp, busy, devices). It drives the
// page's other sections by name — drawRouteControl(), drawRoute(),
// updateStopCard(), checkDeferredStart(), loadStartForm() — and the components
// in their own <script> tags above it (LaunchActuators, LaunchModels,
// compactQueue, hearthRestart, firstRun).
let lastBotState = null; // collapse repeated renders
let lastUp = null;      // the previous poll's up/down, for clearOnEdge
let tick = 0;            // poll counter — actuators refresh on a slower cadence
let cookieMinted = false;  // the browser carrier for plain navigation

// Which finished messages go stale when the bot's state moves: a new session
// makes an old "stopped" stale; any edge makes a finished Start line stale —
// unless a deferred start is still armed and counting.
function clearOnEdge(wasUp, nowUp, armed) {
  if (wasUp === nowUp) return { start: false, stop: false };
  return { start: !armed, stop: nowUp };
}

// Why the last conversation ended, when the answer is one the page can give.
// A sitting that closed itself over a device that never came back exits with
// status 3, one that closed over a device that never arrived with status 4,
// and nothing else uses either — so that number plus the route word is the
// whole of the inference. Pure; empty whenever it cannot say.
function lastExitLine(bot) {
  const exit = bot && bot.last_exit;
  const word = (bot && bot.switches && bot.switches.route) || "";
  if (!exit || word.indexOf("remote:") !== 0) return "";
  const device = word.replace(/^remote:/, "");
  if (exit.code === 3)
    return "the last conversation closed itself: " + device +
           " did not come back within the wait";
  if (exit.code === 4)
    return "the last conversation closed itself: " + device +
           " never connected within the start wait";
  return "";
}

function needToken(prompt) {
  show("tokencard", true); show("switchcard", false); show("stopcard", false);
  show("actuatorcard", false); show("firstrun", false);
  show("modelscard", false); show("sessionscard", false);
  $("statusline").textContent = prompt || "locked — enter your access key";
}

// ── state poll ──────────────────────────────────────────────────────────────

async function refresh() {
  if (!token()) { needToken(); return; }
  let st;
  try { st = await api("/admin/state"); }
  catch (e) {
    if (e.message === "401") { needToken("that key was refused — try again"); return; }
    $("statusline").textContent = "Hearth is not answering — retrying…";
    return;
  }
  show("tokencard", false);
  // A navigation cannot carry the bearer header — mint the cookie carrier once
  // per load or the panel link 401s. Silent on failure: the next poll retries.
  if (!cookieMinted) {
    cookieMinted = true;
    api("/admin/cookie", { method: "POST" }).catch(() => { cookieMinted = false; });
  }
  // Externals move on their own clock (and each probe costs a request), so the
  // actuator card refreshes every fourth poll rather than every one.
  if (tick++ % 4 === 0) {
    LaunchActuators.refresh(api, report);
    LaunchModels.refresh(api, report, botUp);
  }
  const bot = (st.data && st.data.bot) || {};
  const sw = st.data && st.data.switch;
  const ext = (st.data && st.data.externals) || {};
  // The enrolled devices ride the same poll: a phone that pairs in the next
  // room shows up in the selector here within a few seconds, and one that is
  // forgotten leaves it the same way.
  devices = (st.data && st.data.devices) || [];
  drawRouteControl(devices, (bot.switches && bot.switches.route) || "");
  const bits = ["bot: " + (bot.state || "?")];
  if (bot.pid) bits.push("pid " + bot.pid);
  if (bot.uptime_s != null) bits.push("up " + Math.round(bot.uptime_s) + "s");
  for (const k of Object.keys(ext))
    bits.push(k + (ext[k] === true ? " ✓" : ext[k] === false ? " ✗" : " ?"));
  if (sw && sw.phase) bits.push("switch: " + sw.phase + (sw.error ? " (" + sw.error + ")" : ""));
  // With nothing running, the status line is where a person looks to find out
  // what happened — and a conversation that closed ITSELF is the one ending
  // nobody witnessed. Say why, where the state is already reported.
  if (bot.state !== "running" && bot.state !== "starting") {
    const why = lastExitLine(bot);
    if (why) bits.push(why);
  }
  // In-progress session maintenance (compaction): visible state instead of a
  // mystery 409 — the page polls, so the start button simply works once free.
  const maint = (st.data && st.data.maintenance) || [];
  for (const m of maint) {
    if (m.op === "compact")
      bits.push("compacting " + m.character +
                (m.started ? " since " + m.started.slice(11, 16) : ""));
  }
  $("statusline").textContent = bits.join("  ·  ");
  // Parked and FAILED requests — the states no lock can report (a run that
  // dies in its first second never survives a poll).
  compactQueue.render(st.data);
  hearthRestart.render(st.data);
  const parked = firstRun.render(st.data);  // Start parked on the placeholder id

  await checkDeferredStart(st);

  // Never flip cards or re-fill a select under an in-flight action — the card
  // owns a selection the operator may be part-way through making.
  if (busy || card.busy()) return;
  const up = bot.state === "running" || bot.state === "starting";
  botUp = up;
  const stale = clearOnEdge(lastUp, up, !!deferred);
  if (stale.start) card.say("");
  if (stale.stop) sayAt("stopstate", "");
  lastUp = up;
  show("switchcard", !parked);   // the companion card serves BOTH states
  show("startextras", !up);   // session + memory are start-only
  show("stopcard", up);
  if (up) {
    $("runline").textContent = "The companion is up" +
      (bot.managed === false ? " (started from the terminal)" : "") + ".";
    const recall = bot.switches && bot.switches.recall;
    $("recallline").textContent = "Remembering the past: " +
      (recall === true ? "on" : recall === false ? "off" : "—") + " (set at start)";
    drawRoute(bot);
    await updateStopCard(card.character(), bot);
  }
  // Repopulate on each transition in EITHER direction: the verb, the hold row
  // and the current selection all change when the bot comes up or goes down.
  if (bot.state !== lastBotState || choices === null) await loadStartForm();
  lastBotState = bot.state;
}
