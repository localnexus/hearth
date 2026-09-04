// panel_record.js — the control panel's M7 session recording controls.
// Spliced INSIDE control_page.html's own <script> block (ui/panel.py); it takes
// `$`, `status` and `post` from the page and reaches for nothing else, so its
// position in the splice order is free.
//
// M7 session recording — Record toggles gray→red; TTS always, tickboxes add stems.
let recArmed = false, recTimer = null, recT0 = 0;
function renderRec() {
  const b = $('rec');
  b.classList.toggle('rec', recArmed);
  b.textContent = recArmed ? '● REC' : 'Record';
  $('recname').disabled = recArmed;
  $('recmic').disabled = recArmed;
  $('recmusic').disabled = recArmed || $('lmusic').classList.contains('off');
  if (recArmed && !recTimer) {
    recTimer = setInterval(() => {
      const s = Math.floor((Date.now() - recT0) / 1000);
      $('recstate').textContent = '● ' + String(Math.floor(s/60)).padStart(2,'0') + ':' + String(s%60).padStart(2,'0');
    }, 500);
  } else if (!recArmed && recTimer) { clearInterval(recTimer); recTimer = null; $('recstate').textContent = ''; }
}
async function recToggle() {
  if (!recArmed) {
    const j = await post('/record/start', {
      name: $('recname').value.trim(),
      mic: $('recmic').checked, music: $('recmusic').checked});
    if (j && j.ok) { recArmed = true; recT0 = Date.now(); $('recout').textContent = ''; status('recording → ' + j.path); }
    else if (j) status('record error: ' + j.error);
  } else {
    status('finalizing recording…');
    const j = await post('/record/stop', {});
    recArmed = false;
    if (j && j.ok) {
      $('recout').textContent = j.mix ? ('saved: ' + j.mix) : ('nothing captured — stems dir: ' + j.stems);
      status(j.error ? ('saved with warning: ' + j.error) : 'recording saved');
    } else if (j) status('record error: ' + j.error);
  }
  renderRec();
}
$('rec').addEventListener('click', recToggle);
async function loadRecStatus() {
  try {
    const r = await fetch('/record/status');
    const s = await r.json();
    recArmed = s.recording;
    if (recArmed) recT0 = Date.now() - s.elapsed_s * 1000;
    if (!s.music_available) {
      $('lmusic').classList.add('off');
      $('lmusic').title = 'needs a loopback device (BlackHole) + Multi-Output routing — M7 P3';
    } else {
      $('lmusic').title = 'auto-mirrors your current output into ' + s.music_device + ' while recording; restored on stop';
    }
  } catch(e) { /* panel degrades gracefully */ }
  renderRec();
}
loadRecStatus();
