// panel_knobs.js — the control panel's L2 hot knobs.
// Spliced INSIDE control_page.html's own <script> block (ui/panel.py): `$`,
// `status` and `post` come from the page, and renderAgent() from
// panel_status.js, which is spliced just above (a function declaration, so the
// call is safe wherever it lands).
//
// ── L2 hot knobs ────────────────────────────────────────────────────────────────
// Self-gating: the whole section stays hidden unless GET /config answers (i.e.
// features/config_profiles is active). Sliders show EFFECTIVE values (override ??
// baseline); an amber value = an override is set. TTS/voice knobs are per-voice;
// LLM knobs are per-character. Persona is display-only here (file-swap workflow).
let knob = null;        // {schema, overrides, active, defaults, voices, saved}
let selVoice = null;    // the sample the Voice panel is bound to (profile key)

function effVal(sec, key) {
  const ov = knob.overrides[sec] || {};
  if (key in ov) return ov[key];
  const df = knob.defaults[sec] || {};
  return (key in df) ? df[key] : null;
}
function isOverridden(sec, key) { return !!(knob.overrides[sec] && (key in knob.overrides[sec])); }

// The translation layer: plain-language meaning per knob, in terms of how the persona
// thinks (LLM) or sounds (TTS). `low`/`high` name what MORE-toward-that-end feels like,
// so the panel can show which way the current setting leans vs. the shipped baseline.
// `warn` = threshold rules (evaluated live): {lt|gt: bound, msg} — msg shows only while
// the value is past that bound, flagging the adverse effect of that extreme. Bounds are
// deliberately conservative so warnings fire only near genuine trouble (e.g. the top_p 0.1
// pause trap), not for ordinary tuning.
const KNOB_HELP = {
  'llm.temperature': {desc: 'How freely the companion thinks — the spread of word and idea choices.',
    low: 'more focused &amp; predictable', high: 'more inventive &amp; unpredictable',
    warn: [{gt: 1.4, msg: 'very high — wording may drift incoherent or off-character'},
           {lt: 0.15, msg: 'near-deterministic — replies may get repetitive and rigid'}]},
  'llm.reasoning_effort': {desc: 'How much the companion deliberates before answering (none = instinctive, higher = more considered).'},
  'tts.temperature': {desc: 'Emotional expressiveness of the voice — how much the delivery varies.',
    low: 'flatter, more monotone', high: 'more dramatic &amp; animated',
    warn: [{gt: 1.3, msg: 'very high — delivery may turn erratic or garbled'},
           {lt: 0.2, msg: 'very low — may sound robotic or clipped'}]},
  'tts.top_p': {desc: 'How adventurous the vocal choices are (the prosody &amp; pronunciation pool).',
    low: 'tighter &amp; more consistent', high: 'more varied, can wander',
    warn: [{lt: 0.5, msg: 'very low — risk of pauses, stalls, and dropped words'}]},
  'tts.top_k': {desc: 'How many acoustic options the voice weighs at each step.',
    low: 'steadier &amp; more stable', high: 'more variety, less stable',
    warn: [{lt: 50, msg: 'very low — risk of stalls or repetition'}]},
  'tts.repetition_penalty': {desc: 'Pushes the voice off repeating the same cadence.',
    low: 'may fall into sing-song / loops', high: 'more varied rhythm (too high distorts)',
    warn: [{gt: 2.0, msg: 'very high — risk of distortion or dropped sounds'},
           {lt: 1.0, msg: 'below 1 — risk of sing-song looping'}]},
  // CALIBRATION — the [vad] listening tier (per-room/mic; never profile-carried).
  'vad.confidence': {desc: 'How sure the companion must be that a sound is your speech.',
    low: 'hears more — marginal sounds can trigger a turn', high: 'stricter — ignores soft or unclear speech',
    warn: [{gt: 0.9, msg: 'very high — the companion may ignore you'},
           {lt: 0.3, msg: 'very low — background noise can start a turn'}]},
  'vad.start_secs': {desc: 'How long sound must persist before it counts as you talking.',
    low: 'reacts to the briefest sounds', high: 'needs sustained speech before reacting',
    warn: [{gt: 0.6, msg: 'high — your first words may get clipped'}]},
  'vad.stop_secs': {desc: 'The silence after your voice that means you finished.',
    low: 'snappier replies — may cut you off', high: 'waits patiently — slower to reply',
    warn: [{lt: 0.35, msg: 'low — the companion may cut in on mid-sentence breaths'},
           {gt: 1.5, msg: 'high — replies will feel laggy'}]},
  'vad.min_volume': {desc: 'How loud a sound must be to count as speech.',
    low: 'even quiet speech counts', high: 'only clear, louder speech counts',
    warn: [{gt: 0.8, msg: 'high — you would have to speak up'}]},
};
function knobHelp(sec, key) { return KNOB_HELP[sec + '.' + key] || {desc: ''}; }

// First threshold rule the current value trips, if any (for the live ⚠ warning line).
function warnText(sec, key, val) {
  const rules = knobHelp(sec, key).warn;
  if (!rules) return '';
  for (const r of rules) {
    if ((r.lt !== undefined && val < r.lt) || (r.gt !== undefined && val > r.gt)) return r.msg;
  }
  return '';
}

// Which way the live value leans vs. the persisted baseline, in the knob's own words.
function leanText(sec, key, val) {
  const h = knobHelp(sec, key);
  if (!('low' in h)) return '';
  const df = (knob.defaults[sec] || {})[key];
  if (df === undefined || df === null) return '';
  const eps = (typeof df === 'number' && df > 0) ? Math.abs(df) * 0.02 : 0.0001;
  if (Math.abs(val - df) <= eps) return 'at the default';
  return val < df ? h.low : h.high;
}

function sliderRow(sec, key, spec) {
  const eff = effVal(sec, key);
  const step = spec.type === 'int' ? 1 : ((spec.max - spec.min) > 3 ? 1 : 0.01);
  const h = knobHelp(sec, key);
  const row = document.createElement('div'); row.className = 'kfield';
  row.innerHTML =
    `<div class="krow"><span class="kname">${key}</span>` +
    `<input type="range" min="${spec.min}" max="${spec.max}" step="${step}" value="${eff === null ? spec.min : eff}">` +
    `<span class="kval"></span></div>` +
    `<div class="khelp"></div>` +
    `<div class="kwarn hidden"></div>`;
  const inp = row.querySelector('input'), val = row.querySelector('.kval'),
        help = row.querySelector('.khelp'), warn = row.querySelector('.kwarn');
  // no baseline recorded AND no override → there is no truthful
  // position to draw. Show '—' with the slider disabled rather than silently
  // parking it at spec.min while the engine runs its internal defaults (the
  // fresh-install display falsehood; installs with a persisted baseline never
  // enter this branch).
  if (eff === null) {
    inp.disabled = true;
    val.textContent = '—';
    help.innerHTML = h.desc + ' <span class="lean">— no persisted baseline; knob disabled</span>';
    return row;
  }
  const paintHelp = v => {
    const lean = leanText(sec, key, v);
    help.innerHTML = h.desc + (lean ? ` <span class="lean">— now: ${lean}</span>` : '');
    const w = warnText(sec, key, v);
    warn.innerHTML = w ? ('⚠ ' + w) : '';
    warn.classList.toggle('hidden', !w);
  };
  const paint = () => {
    val.textContent = inp.value; val.classList.toggle('dirty', isOverridden(sec, key));
    paintHelp(parseFloat(inp.value));
  };
  paint();
  inp.addEventListener('input', () => { val.textContent = inp.value; paintHelp(parseFloat(inp.value)); });
  inp.addEventListener('change', async () => {
    const v = spec.type === 'int' ? parseInt(inp.value, 10) : parseFloat(inp.value);
    const j = await post('/config', {[sec]: {[key]: v}});
    if (j && j.ok) { knob.overrides = j.overrides; paint(); status(`${sec}.${key} → ${v}`); }
    else if (j) status('knob error: ' + j.error);
  });
  return row;
}

function enumRow(sec, key, spec) {
  const eff = effVal(sec, key);
  const row = document.createElement('div'); row.className = 'kfield';
  // enum flavor: with no effective value, an unguarded <select>
  // silently shows its first option as if chosen. A disabled '—' placeholder
  // shows the truth; picking a real value stays a deliberate, working write.
  const opts = (eff === null ? '<option selected disabled>—</option>' : '') +
    spec.values.map(o => `<option ${o === eff ? 'selected' : ''}>${o}</option>`).join('');
  row.innerHTML =
    `<div class="krow"><span class="kname">${key}</span><select>${opts}</select><span class="kval"></span></div>` +
    `<div class="khelp">${knobHelp(sec, key).desc}</div>`;
  const sel = row.querySelector('select'), val = row.querySelector('.kval');
  const paint = () => val.classList.toggle('dirty', isOverridden(sec, key));
  paint();
  sel.addEventListener('change', async () => {
    const j = await post('/config', {[sec]: {[key]: sel.value}});
    if (j && j.ok) { knob.overrides = j.overrides; paint(); status(`${sec}.${key} → ${sel.value}`); }
    else if (j) status('knob error: ' + j.error);
  });
  return row;
}

function renderKnobs() {
  $('k-char').textContent = knob.active.character;
  $('k-voice').textContent = selVoice;
  const llm = $('k-llm-rows'); llm.innerHTML = '';
  if (knob.schema.llm.temperature) llm.appendChild(sliderRow('llm', 'temperature', knob.schema.llm.temperature));
  if (knob.schema.llm.reasoning_effort) llm.appendChild(enumRow('llm', 'reasoning_effort', knob.schema.llm.reasoning_effort));
  const sel = $('k-sample');
  sel.innerHTML = knob.voices.map(v =>
    `<option ${v === selVoice ? 'selected' : ''}>${v}${(knob.saved.voices || []).includes(v) ? ' ★' : ''}</option>`).join('');
  const tts = $('k-tts-rows'); tts.innerHTML = '';
  ['temperature', 'top_p', 'top_k', 'repetition_penalty'].forEach(k => {
    if (knob.schema.tts[k]) tts.appendChild(sliderRow('tts', k, knob.schema.tts[k]));
  });
  // CALIBRATION tier — whole group hides if the server predates the [vad] schema.
  $('kp-listen').classList.toggle('hidden', !knob.schema.vad);
  const vad = $('k-vad-rows'); vad.innerHTML = '';
  ['confidence', 'start_secs', 'stop_secs', 'min_volume'].forEach(k => {
    if (knob.schema.vad && knob.schema.vad[k]) vad.appendChild(sliderRow('vad', k, knob.schema.vad[k]));
  });
}

async function fetchKnobState() {
  const [c, p] = await Promise.all([
    fetch('/config').then(r => r.json()),
    fetch('/config/profiles').then(r => r.json()),
  ]);
  if (!c.ok || !p.ok) throw new Error(c.error || p.error || 'knob state unavailable');
  return {
    schema: c.schema, overrides: c.overrides || {},
    active: p.active,
    // texture-tier baselines (llm/tts) ride /config/profiles; the calibration-tier
    // baseline (vad) rides /config — merge the two sources.
    defaults: Object.assign({}, p.defaults || {}, c.defaults || {}),
    voices: p.voices || [], saved: p.saved || {voices: []},
  };
}
function deriveSelVoice() {
  // selVoice used to derive ONLY at
  // page load, so a tab open across an active.toml edit/restart kept presenting
  // (and profile-targeting) the previous voice. Re-derive from every server
  // fetch instead: a live [voice] override names the bound cut (its ref_wav's
  // bundle dir); no override → the dropdown IS the baseline. A dropdown pick is
  // never clobbered — the pick writes the very override this reads back.
  const ov = knob.overrides && knob.overrides.voice && knob.overrides.voice.ref_wav;
  if (ov) {
    const m = String(ov).replace(/\\/g, '/').match(/\/([^/]+)\/[^/]+$/);
    if (m && knob.voices.includes(m[1])) selVoice = m[1];
    return;  // unmappable override (out-of-tree ref_wav): keep current selection
  }
  selVoice = knob.active.voice;
}
async function refreshKnobs() {
  try { knob = await fetchKnobState(); deriveSelVoice(); renderKnobs(); renderAgent(); }
  catch (e) { status('knob refresh error: ' + e); }
}

function wireKnobButtons() {
  // Sample switch: select the clip AND load its saved preset (baseline if none).
  $('k-sample').addEventListener('change', async e => {
    selVoice = e.target.value.replace(/ ★$/, '');
    const j = await post('/config/profiles/load', {scope: 'voice', character: knob.active.character, voice: selVoice});
    if (j && j.ok) { status('voice → ' + selVoice); await refreshKnobs(); }
    else if (j) status('voice error: ' + j.error);
  });
  document.querySelectorAll('#knobs [data-save]').forEach(b => b.addEventListener('click', async () => {
    const scope = b.dataset.save, body = {scope, character: knob.active.character};
    if (scope === 'voice') body.voice = selVoice;
    const j = await post('/config/profiles/save', body);
    if (j && j.ok) { status(`saved ${scope} preset`); await refreshKnobs(); }
    else if (j) status('save error: ' + j.error);
  }));
  document.querySelectorAll('#knobs [data-load]').forEach(b => b.addEventListener('click', async () => {
    const j = await post('/config/profiles/load', {scope: b.dataset.load, character: knob.active.character, voice: selVoice});
    if (j && j.ok) { status(`loaded ${b.dataset.load} preset`); await refreshKnobs(); }
    else if (j) status('load error: ' + j.error);
  }));
  document.querySelectorAll('#knobs [data-reset]').forEach(b => b.addEventListener('click', async () => {
    const j = await post('/config/profiles/reset', {scope: b.dataset.reset});
    if (j && j.ok) { status(`reset ${b.dataset.reset}`); await refreshKnobs(); }
    else if (j) status('reset error: ' + j.error);
  }));
  // CALIBRATION reset — clears the [vad] overrides only (back to config/vad.toml).
  document.querySelectorAll('#knobs [data-vadreset]').forEach(b => b.addEventListener('click', async () => {
    const j = await post('/config', {clear: ['vad.confidence', 'vad.start_secs', 'vad.stop_secs', 'vad.min_volume']});
    if (j && j.ok) { knob.overrides = j.overrides; status('listening → calibration baseline'); await refreshKnobs(); }
    else if (j) status('reset error: ' + j.error);
  }));
  $('k-reset-all').addEventListener('click', async () => {
    const j = await post('/config/profiles/reset', {scope: 'all'});
    if (j && j.ok) { status('restored ALL to defaults'); await refreshKnobs(); }
    else if (j) status('reset error: ' + j.error);
  });
}

async function initKnobs() {
  let state;
  try {
    const probe = await fetch('/config');
    if (!probe.ok) return;            // 404 → feature inert; leave the section hidden
    state = await fetchKnobState();
  } catch (e) { return; }             // unreachable → stay hidden, panel unchanged
  knob = state;
  deriveSelVoice();
  wireKnobButtons();
  renderKnobs();
  renderAgent();
  $('knobs').classList.remove('hidden');
}
initKnobs();
