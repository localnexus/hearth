// panel_turn.js — the last-reply echo that sits above the compose box.
// Spliced INSIDE control_page.html's own <script> block (ui/panel.py), so `$`
// comes from the page. Rides GET /turn (features/turn_echo.py), which reads the
// live LLMContext the panel already holds — there is no tap behind this.
//
// Self-gating like the knobs and memory sections: no /turn route (the feature
// module not imported) means the block stays hidden and the panel is what it
// was. Text is written with textContent, never innerHTML — her reply is model
// output, and the panel renders it as the plain text it is. Markdown rendering
// belongs to the text-surface build, not to a status echo.
const TURN_POLL_MS = 2000;
let lastTurnSeq = -1;   // seq of the message currently painted; -1 = nothing yet

// Two states worth telling apart: her reply is the newest message (paint it), or
// yours is (she is composing — keep the previous reply on screen and say so,
// rather than blanking the block or echoing your own words back at you).
function renderTurn(t) {
  const el = $('lastturn'), body = $('lastturn-txt'), who = $('lastturn-who');
  if (!t.role) { el.classList.add('hidden'); return; }
  if (t.role === 'assistant') {
    if (t.seq !== lastTurnSeq) {
      body.textContent = t.text;
      body.scrollTop = 0;   // a long reply is read from its start
      lastTurnSeq = t.seq;
    }
    who.textContent = 'last reply';
    who.classList.remove('waiting');
  } else {
    // Waiting. If nothing has been painted yet this sitting there is no reply to
    // keep, so the block carries the cue alone.
    who.textContent = 'answering…';
    who.classList.add('waiting');
  }
  el.classList.remove('hidden');
}

async function pollTurn() {
  try {
    const r = await fetch('/turn');
    if (!r.ok) throw 0;
    const t = await r.json();
    if (!t.ok) throw 0;
    renderTurn(t);
  } catch(e) { $('lastturn').classList.add('hidden'); }
}

setInterval(pollTurn, TURN_POLL_MS);
pollTurn();  // paint immediately on load
