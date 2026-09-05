// hearth_restart.js — the launch page's "Restart Hearth" card.
//
// Hearth (the facade) can restart itself only when a keeper stands behind it —
// launchd on the supervised install — so the card draws only while
// /admin/state reports one (keeper: "launchd"), and stays away on a terminal
// run, where the same request would simply end Hearth. The companion lives in
// its own process group and keeps talking through the restart; the page's own
// poll reconnects. Host supplies a #hearthcard mount, the authed api() and
// report(); render(state) each poll.
window.HearthRestart = (function () {
  function mount(host, api, report) {
    host.replaceChildren();
    const h = document.createElement("h2");
    h.textContent = "Hearth";
    const p = document.createElement("p");
    p.className = "note";
    p.textContent = "Restart the program serving these pages — the way to pick up an " +
      "update. A companion who is talking keeps talking; this page reconnects on its own.";
    const b = document.createElement("button");
    b.textContent = "Restart Hearth";
    let busy = false;
    b.addEventListener("click", async () => {
      if (busy) return;
      busy = true; b.disabled = true;
      report("Hearth is restarting — this page reconnects in a moment…");
      try {
        const r = await api("/admin/daemon/restart", { json: {} });
        if (!(r.data && r.data.ok)) report((r.data && r.data.error) || ("refused (" + r.status + ")"), true);
      } catch (e) {
        // The exit races the reply; a dropped connection here is the restart happening.
        if (e.message === "401") report("key refused", true);
      } finally { setTimeout(() => { busy = false; b.disabled = false; }, 5000); }
    });
    host.appendChild(h); host.appendChild(p); host.appendChild(b);
    return {
      render(data) { host.classList.toggle("hidden", !(data && data.keeper)); },
    };
  }
  return { mount: mount };
})();
