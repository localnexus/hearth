// panel_manual.js — the control panel's destinations rail and manual reader.
// Spliced INSIDE control_page.html's own <script> block (ui/panel.py); it takes
// `$` and `status` from the page and reaches for nothing else, so unlike the
// other two panel modules its position in the splice order is free.
//
// ── Destinations rail + manual pane ──────────────────────────
// Self-gating like initKnobs: no /manual routes → the rail stays hidden and the page
// is byte-identical in behavior. Switching destinations toggles visibility only —
// nothing unloads, so the status poll, draft text, mute/PTT state, and an in-flight
// recording all survive by construction (the requirement's reset class vanishes).
let manualPages = [], manualCur = null;
function showDest(d) {
  $('dest-controls').classList.toggle('hidden', d !== 'controls');
  $('dest-manual').classList.toggle('hidden', d !== 'manual');
  $('dest-b-controls').classList.toggle('active', d === 'controls');
  $('dest-b-manual').classList.toggle('active', d === 'manual');
  if (d === 'manual' && !manualCur && manualPages.length) loadManualPage(manualPages[0].name);
}
function buildManualNav() {
  $('mnav').innerHTML = '';
  manualPages.forEach(p => {
    const b = document.createElement('button');
    b.textContent = p.title; b.dataset.page = p.name; b.title = p.name;
    b.addEventListener('click', () => loadManualPage(p.name));
    $('mnav').appendChild(b);
  });
}
async function loadManualPage(name) {
  try {
    const r = await fetch('/manual/page/' + encodeURIComponent(name));
    const j = await r.json();
    if (!j.ok) { status('manual error: ' + j.error); return; }
    manualCur = name;
    $('mcontent').innerHTML = j.html;
    window.scrollTo(0, 0);
    document.querySelectorAll('#mnav button').forEach(b =>
      b.classList.toggle('active', b.dataset.page === name));
    if (j.degraded) status('manual: page served raw (renderer degraded)');
  } catch (e) { status('manual error: ' + e); }
}
// In-pane link interception: rendered same-dir links carry data-page (no navigation).
$('mcontent').addEventListener('click', e => {
  const a = e.target.closest('a[data-page]');
  if (a) { e.preventDefault(); loadManualPage(a.dataset.page); }
});
async function initManual() {
  let j;
  try {
    const r = await fetch('/manual/index');
    if (!r.ok) return;               // 404 → feature inert; rail stays hidden
    j = await r.json();
  } catch (e) { return; }             // unreachable → stay hidden, panel unchanged
  if (!j.ok || !j.pages || !j.pages.length) return;
  manualPages = j.pages;
  buildManualNav();
  $('rail').classList.remove('hidden');
  document.body.classList.add('railed');
  $('dest-b-controls').addEventListener('click', () => showDest('controls'));
  $('dest-b-manual').addEventListener('click', () => showDest('manual'));
}
initManual();
