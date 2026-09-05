// first_run_offer.js — the launch page's First-run offer.
//
// The entry condition of the first-run path: while the selected model's id is
// still the shipped placeholder, or nothing on this install has a session yet,
// the launch page points at /admin/first-run — and while the id is the
// placeholder it parks its own Start, because a bot started now would ask the
// server for a model that does not exist. Both facts ride /admin/state as
// first_run: {needs_model, fresh}; an install too broken to say answers null
// and this draws nothing. Host page supplies a #firstrun mount and calls
// render(state); the return value is "keep Start parked".
window.firstRun = (function () {
  function render(data) {
    const fr = (data && data.first_run) || null;
    const on = !!(fr && (fr.needs_model || fr.fresh));
    const host = document.getElementById("firstrun");
    host.classList.toggle("hidden", !on);
    if (!on) return false;
    host.replaceChildren();
    const h = document.createElement("h2");
    h.textContent = "First run";
    const p = document.createElement("p");
    p.textContent = fr.needs_model
      ? "The selected model config still carries the shipped placeholder id, so " +
        "Start is parked. The first-run page lists what your server serves and " +
        "records your pick."
      : "Nothing has been said on this install yet. The first-run page walks the " +
        "first sitting: server, model, companion, and the first words.";
    const a = document.createElement("a");
    a.href = "/admin/first-run";
    a.textContent = "Walk through the first run →";
    const q = document.createElement("p");
    q.appendChild(a);
    host.appendChild(h); host.appendChild(p); host.appendChild(q);
    return !!fr.needs_model;
  }
  return { render: render };
})();
