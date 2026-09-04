// compact_queue.js — the compaction queue, rendered on the launch page.
//
// Why this is a component and not three lines in the status bar: a failure is
// the one queue state nobody is watching for. A running compaction already
// shows up in the status line (the maintenance lock), and a parked one resolves
// itself. A `.failed` breadcrumb is never retried by design — so if it is not
// on screen, the operator's next move is to press the button again and watch
// nothing happen for ten minutes.
//
// Names and states only; the server sends no session content and this draws
// none. Host page supplies a #compactqueue mount and calls render(state).

window.compactQueue = (function () {
  const STYLE = {
    failed:  ["✗", "#c0392b"],
    running: ["●", "#2c7"],
    parked:  ["·", "#888"],
  };

  function line(e) {
    const [glyph, color] = STYLE[e.state] || ["?", "#888"];
    const row = document.createElement("div");
    row.style.cssText = "margin:2px 0;color:" + color;
    let text = glyph + " " + e.state + " — " + e.session + " (" + e.character + ")";
    if (e.state === "failed") {
      // The reason is the whole point of showing this at all.
      text += e.step ? " · stopped at " + e.step : "";
      text += e.error ? " · " + e.error : " · reason not recorded — see logs/compact-auto.log";
      text += " · press Compact again to retry";
    } else if (e.state === "parked") {
      text += " · waiting for a free stage";
    }
    if (e.requested) text += " · asked " + e.requested.slice(11, 16);
    row.textContent = text;
    return row;
  }

  function render(st) {
    const mount = document.getElementById("compactqueue");
    if (!mount) return;
    const q = (st && st.compact_queue) || [];
    mount.textContent = "";
    if (!q.length) { mount.classList.add("hidden"); return; }
    mount.classList.remove("hidden");
    const head = document.createElement("div");
    head.style.cssText = "font-weight:600;margin-top:6px";
    head.textContent = "Compaction queue";
    mount.appendChild(head);
    for (const e of q) mount.appendChild(line(e));
  }

  return { render };
})();
