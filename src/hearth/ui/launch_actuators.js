// launch_actuators.js — the launch page's "Externals" card: declared bring-up
// commands, one row each, with the Run button.
//
// A run holds the request until the command finishes — the wait IS the
// spinner. A guarded actuator (guard = "companion" in serve.toml) answers 409
// with the guard named while a companion is running: whatever the command
// frees, the next turn pays to bring back (freeing the model server's models
// is the case that motivated it — a live session owns its model's residency).
// The page then asks, in words, and only a confirmed press re-sends with
// ?force=1. Host supplies the authed api() and report(); refresh() each poll.
window.LaunchActuators = (function () {
  const busy = new Set(); // runs in flight — never re-render under one

  function mk(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  function stateOf(a) {
    const bits = [];
    if (a.probe === true) bits.push("up ✓");
    else if (a.probe === false) bits.push("down ✗");
    if (a.running) bits.push("running…");
    const last = a.last;
    if (last) {
      bits.push("last " + (last.ok ? "ok" : last.timed_out ? "timed out"
                           : "exit " + last.exit) + " · " + last.duration_s + "s");
    }
    if (a.guard === "companion") bits.push("held while a companion is running");
    return bits.join("  ·  ");
  }

  async function refresh(api, report) {
    if (busy.size) return;   // a run is holding the request — leave the rows be
    let r;
    try { r = await api("/admin/actuators"); } catch { return; }
    const acts = (r.data && r.data.actuators) || {};
    const names = Object.keys(acts).sort();
    const card = document.getElementById("actuatorcard");
    if (card) card.classList.toggle("hidden", names.length === 0);
    const host = document.getElementById("actuators");
    if (!host) return;
    host.innerHTML = "";        // rebuilt from nodes below — never from API text
    for (const n of names) {
      const a = acts[n];
      const left = mk("div", "grow", n);
      if (a.note) left.appendChild(mk("div", "note", a.note));
      left.appendChild(mk("div", "state", stateOf(a)));
      const btn = mk("button", "", "Run");
      btn.disabled = !!a.running;
      btn.addEventListener("click", () => run(api, report, n, btn, false));
      const row = mk("div", "row");
      row.appendChild(left); row.appendChild(btn);
      host.appendChild(row);
    }
  }

  async function run(api, report, name, btn, force) {
    busy.add(name);
    btn.disabled = true;
    btn.textContent = "running…";
    report("running " + name + " — this holds until the command finishes");
    let again = false;
    try {
      const r = await api("/admin/actuators/" + encodeURIComponent(name) + "/run"
                          + (force ? "?force=1" : ""), { method: "POST" });
      const d = r.data || {};
      if (r.status === 409 && d.guard === "companion") {
        // The guard, in words: a confirmed press is the only way through.
        again = window.confirm(
          "A companion is running. " + name + " frees something the next turn "
          + "would have to bring back — a long pause, or one on every turn.\n\n"
          + "Run " + name + " anyway?");
        if (!again) report(name + " held — a companion is running", true);
      }
      else if (r.status === 409) report(name + " is already running", true);
      else if (r.status === 404) report("unknown actuator: " + name, true);
      else if (d.ok) report(name + " ok in " + d.duration_s + "s");
      else {
        // exit === null is a spawn that never happened (bad path, permissions) —
        // a different failure from a command that ran and returned non-zero.
        const why = d.timed_out ? "timed out"
                  : d.exit === null || d.exit === undefined ? "could not start"
                  : "exit " + d.exit;
        report(name + " failed: " + why + " — the log is on the machine", true);
      }
    } catch (e) {
      report(e.message === "401" ? "key refused" : name + " failed: " + e.message, true);
    } finally {
      busy.delete(name);
      btn.textContent = "Run"; btn.disabled = false;
      if (again) run(api, report, name, btn, true);
      else refresh(api, report);
    }
  }

  return { refresh };
})();
