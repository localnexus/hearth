// firstrun_listen.js — steps 2 and 3: the start, and the first words.
// Spliced INSIDE first_run_page.html's own <script> block after
// firstrun_check.js. Owns the shared switch card — the same ui/switch_card.js
// the launch page and the panel carry — and the listen line; refresh() in
// firstrun_check.js hands it each state payload through renderListen().
let botUp = false;         // {start:true} rides a cold start only, never a warm switch
let lastBotState = null;   // the card re-populates on a transition, never on a poll
let cardLoaded = false;

const card = window.HearthSwitchCard.mount($("switchmount"), {
  cls: { row: "row", name: "", btns: "row", state: "state", legend: "note" },
  selfDies: false,          // the facade outlives the bot it starts
  state: async () => {
    const sw = await api("/admin/switch");
    return sw.status === 200 ? sw.data : null;
  },
  live: async () => (await api("/admin/switch/live")).data,
  submit: (body) => api("/admin/switch", { json: body }),
  // A cold start opens a NEW session — a first sitting has nothing to resume —
  // and carries no memory rider: the sitting runs under the install's memory
  // gate as the bootstrap left it (off unless asked).
  extraBody: () => (botUp ? {} : { start: true, mode: "new" }),
  onApplied: () => refresh(),
});

function agentLine(eng) {
  if (!eng || !eng.character) return "";
  return "  ·  " + eng.character + " · voice " + (eng.voice || "?") +
         " · model " + (eng.model_id || "?");
}

async function renderListen(d) {
  const bot = d.bot || {};
  const up = bot.state === "running" || bot.state === "starting";
  botUp = up;
  if (!card.busy() && (bot.state !== lastBotState || !cardLoaded))
    cardLoaded = await card.load();
  lastBotState = bot.state;
  const line = $("listenline");
  if (!up) {
    line.textContent = "waiting for Start (step 2)" +
      (typeof bot.last_exit === "number"
        ? " — the last run exited with " + bot.last_exit + "; logs/bot.log has the reason"
        : "");
    show("donecard", false);
    return;
  }
  // Up: the bot's own counters, through the facade's proxy. Until the panel
  // answers, the bot is still warming (10–20 s; a first start compiles kernels).
  let usage = null, eng = null;
  try {
    usage = (await api("/usage")).data;
    eng = (await api("/engine")).data;
  } catch (e) { /* proxy not ready yet — the next poll asks again */ }
  if (!usage || typeof usage.turns !== "number") {
    line.textContent = "starting up" +
      (bot.uptime_s != null ? " (" + Math.round(bot.uptime_s) + " s)" : "") + "…";
    return;
  }
  if (usage.turns === 0) {
    line.textContent = "listening — say something" + agentLine(eng);
    show("donecard", false);
  } else {
    line.textContent = "heard you and answered — " + usage.turns +
      (usage.turns === 1 ? " turn" : " turns") + " ✓" + agentLine(eng);
    show("donecard", true);
  }
}
