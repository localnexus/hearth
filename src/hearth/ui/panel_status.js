// panel_status.js — the control panel's status block and its poll timers.
// Spliced INSIDE control_page.html's own <script> block (ui/panel.py), so `$`,
// `status` and `post` come from the page and this file's declarations are the
// page's top-level bindings. Splice order matters here: renderAgent() reads
// `knob` and `selVoice`, which panel_knobs.js declares with `let` AFTER this
// file — safe only because the read happens post-await, never during load.
//
// Phase 1 status block.
// Engine facts (allotted / model max / Model ID / provider) ride /engine — fetched
// on load and re-fetched every 60 s. The server re-polls LM Studio on the same
// cadence so a mid-run model swap/unload surfaces as fresh facts or
// honest '—' dashes instead of a stale line. Dynamic per-turn counts (held-in-ctx /
// growth / cumulative input) ride the polled /usage snapshot.
const DASH = '—';
const fmt = n => (n === null || n === undefined) ? DASH : Number(n).toLocaleString();
let engine = {provider: null, model_id: null, allotted: null, model_max: null, reliable: null, session: null, character: null, voice: null, memory_mode: null};

function renderEngine() {
  $('s-engine').textContent =
    `Engine | Inference Provider: ${engine.provider || DASH} · Model ID: ${engine.model_id || DASH}`;
}

// The active persona: character + voice. Baseline rides /engine (the SESSION's
// startup-resolved voice — the startup scrub clears [voice] overrides, so with no
// live override this IS the voice speaking). The baseline alone read naturally
// as "the voice speaking now,"
// which is false mid-audition — with a live [voice] override bound, show both.
function renderAgent() {
  let v = engine.voice || DASH;
  if (knob && knob.overrides && knob.overrides.voice && selVoice
      && selVoice !== (engine.voice || null)) {
    v = `${engine.voice || DASH} (base) · now: ${selVoice}`;
  }
  $('s-agent').textContent =
    `Agent  | Name: ${engine.character || DASH} · Voice: ${v}`;
  const pv = engine.persona && engine.persona !== 'default' ? `persona.${engine.persona}.md` : 'persona.md';
  if ($('k-persona')) $('k-persona').textContent = `· ${pv}`;
}

async function loadEngine() {
  try {
    const r = await fetch('/engine');
    engine = await r.json();
  } catch(e) { /* keep the all-null default → dashes */ }
  renderEngine();
  renderAgent();
}

// Tier-2 → Phase 1: live per-turn token snapshot (polled).
async function pollUsage() {
  try {
    const r = await fetch('/usage');
    const u = await r.json();
    const held = u.held_in_ctx;
    const allot = engine.allotted;
    // Gauge against the MEASURED reliable line; fall back to the advertised window
    // when reliable is absent (older config) → today's behavior. Zones warn BEFORE
    // the line: ok < 75% · warn 75–100% · over ≥ 100% of the reliable budget.
    const budget = (engine.reliable !== null && engine.reliable !== undefined) ? engine.reliable : allot;
    const ratio = budget ? (held / budget) : null;
    const pct = (ratio === null) ? DASH : (ratio * 100).toFixed(1) + '%';
    const remaining = (budget !== null && budget !== undefined) ? fmt(budget - held) : DASH;
    const zone = (ratio === null) ? 'na' : (ratio >= 1 ? 'over' : (ratio >= 0.75 ? 'warn' : 'ok'));
    const tok = $('s-tokens');
    tok.className = 'zone-' + zone;
    tok.textContent =
      `Tokens | ${fmt(held)}${u.estimated ? ' est.' : ''} held [${pct} of ${fmt(budget)} reliable] · ${remaining} to line · advertised ${fmt(allot)} · model max: ${fmt(engine.model_max)}`;
    $('ctxwarn').classList.toggle('hidden', zone === 'ok' || zone === 'na');
    $('s-misc').textContent =
      `Misc   | Session: ${engine.session || DASH} · Memory: ${engine.memory_mode || DASH} · Turns: ${fmt(u.turns)} · net turn growth: ${fmt(u.net_turn_growth)} · total tok. xmitted: ${fmt(u.prompt)}`;
    $('leak').classList.toggle('hidden', !u.leak);
  } catch(e) { /* transient; next tick retries */ }
}
// Memory status line (read-only tap): rides /memory (features/memory_status).
// Self-gating like the knobs section — hidden unless the route answers with an
// attached seam (the sitting's MODE alone already shows on the Misc line).
// Attribution names the backend that ACTUALLY answered: a floor fallback shows
// as "floor", never as the primary.
async function pollMemory() {
  const el = $('s-memory');
  try {
    const r = await fetch('/memory');
    if (!r.ok) throw 0;
    const m = await r.json();
    if (!m.ok || !m.attached || !m.seam) { el.classList.add('hidden'); return; }
    const s = m.seam;
    const pt = s.per_turn || {};
    const ptTxt = pt.chat ? ('chat' + (pt.voice ? ' + voice' : '')) : 'off';
    const open = s.open_recall
      ? `${fmt(s.open_recall.count)} at open via ${s.open_recall.source || DASH}` : DASH;
    const turn = s.turn_recall
      ? `${fmt(s.turn_recall.extras)} extra(s) via ${s.turn_recall.source || DASH}` : DASH;
    $('s-memory-txt').textContent =
      `Memory | ${s.backend || DASH}${s.retain === false ? ' (recall-only)' : ''} · per-turn: ${ptTxt} · recalled: ${open} · last turn: ${turn}`;
    // Per-turn-voice pause/resume: a RUNTIME-ONLY poke of the live seam
    // (decision signed 2026-09-02) — self-gating: shown only when bot.py built
    // the prefetch processor this sitting (a voice-off start has nothing to
    // light) and the chat gate is on for the current companion.
    const vbtn = $('s-memory-voice');
    if (m.voice_prefetch_built && pt.chat) {
      vbtn.textContent = pt.voice ? 'pause voice recall' : 'resume voice recall';
      vbtn.classList.remove('hidden');
    } else {
      vbtn.classList.add('hidden');
    }
    el.classList.remove('hidden');
  } catch(e) { el.classList.add('hidden'); }
}

$('s-memory-voice').addEventListener('click', async () => {
  const btn = $('s-memory-voice');
  const resume = btn.textContent.startsWith('resume');
  btn.disabled = true;
  try {
    const r = await fetch('/memory/per-turn-voice', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({on: resume}),
    });
    const d = await r.json();
    if (!d.ok) throw 0;
  } catch(e) { /* the poll repaints the true state either way */ }
  btn.disabled = false;
  pollMemory();
});

loadEngine();  // paint immediately on load
setInterval(loadEngine, 60000);  // slow re-fetch — matches bot.py's engine re-poll cadence
setInterval(pollUsage, 1500);
pollUsage();  // paint immediately on load
setInterval(pollMemory, 5000);  // per-turn attribution moves at conversation pace
pollMemory();
