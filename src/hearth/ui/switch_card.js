// switch_card.js — the companion switcher, ONE implementation, two hosts.
//
// The control panel (:65000) and the facade's launch page (:65001) both offer
// "pick who's live". They used to build that form twice, which is how the two
// surfaces drifted apart: the panel grew persona + model pickers, the launch
// page grew session + memory, and neither learned the other's fields. This file
// owns the SELECTION half — character, voice, persona, model, hold — for both.
//
// What stays with the host, because it genuinely differs:
//   • transport   — the panel calls /companion (an unauthed loopback relay),
//                   the facade calls /admin/switch behind the bearer;
//   • context     — the launch page adds session + memory-mode on a cold start
//                   (neither can ride a live switch: the daemon 409s memory);
//   • aftermath   — the panel DIES during a restart and must watch for its own
//                   return, the facade page stays up and just re-polls.
//
// What must never diverge — the field set, the body shape, and the live-vs-
// restart reading — lives here. The daemon owns the actual routing decision
// (switch.py: "Same button either way"); this card only reports it honestly.
//
// Secret hygiene: names and states only. Nothing here reads a token; the host's
// submit() adapter carries whatever credential its door needs.
"use strict";

window.HearthSwitchCard = (function () {

  const SELECTION = ["character", "voice", "persona", "model"];

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;   // never innerHTML: names are data
    return n;
  }

  function fill(sel, names, want) {
    sel.innerHTML = "";
    for (const n of names) {
      const o = el("option", "", n);
      o.value = n;
      sel.appendChild(o);
    }
    if (want && names.includes(want)) sel.value = want;
  }

  /**
   * Build the card into `mount`.
   *
   * adapters:
   *   state()      -> the /admin/switch GET payload ({current, choices, bot})
   *   live()       -> the bot's /switch/live describe, or {ok:false, reason}
   *   submit(body) -> {status, data} for the switch POST
   *   extraBody()  -> fields the HOST adds (session, memory, start) — optional
   *   onApplied()  -> called once the switch has actually landed — optional
   *   selfDies     -> true when a restart kills this page (the panel)
   *   cls          -> host class names, so the card wears the host's skin
   */
  function mount(mountEl, adapters) {
    const cls = Object.assign(
      { head: "", legend: "", row: "", name: "", btns: "", state: "" },
      adapters.cls || {});
    let choices = null;
    let busy = false;

    // ── chrome ──────────────────────────────────────────────────────────────
    const head = el("div", cls.head, "COMPANION — ");
    const cur = el("span", "", "…");
    head.appendChild(cur);
    const legend = el("div", cls.legend);
    legend.textContent =
      "Pick who's live. The daemon writes active.toml (the previous file is kept " +
      "as .prev) and applies it the lightest way it can: LIVE at the next words " +
      "when every changed piece has a live path (persona · voice · resident " +
      "model), otherwise a warm restart of the voice bot. The LLM server is " +
      "never touched either way. Models marked ● are loaded on the server now.";

    const selects = {};
    const rows = [];
    for (const [key, label] of [["character", "Character"], ["voice", "Voice"],
                                ["persona", "Persona"], ["model", "Model"]]) {
      const row = el("div", cls.row);
      row.appendChild(el("span", cls.name, label));
      const sel = el("select");
      sel.id = "hsc-" + key;
      selects[key] = sel;
      row.appendChild(sel);
      rows.push(row);
    }

    const holdRow = el("label", cls.row);
    holdRow.title = "drop a hold marker before stopping, so the current session " +
                    "is kept (named) instead of discarded";
    const hold = el("input");
    hold.type = "checkbox";
    holdRow.appendChild(hold);
    holdRow.appendChild(document.createTextNode(" keep this session (hold)"));

    const btns = el("div", cls.btns);
    const go = el("button", "", "Switch");
    const state = el("span", cls.state);
    btns.appendChild(go);
    btns.appendChild(document.createTextNode(" "));
    btns.appendChild(state);

    mountEl.appendChild(head);
    mountEl.appendChild(legend);
    for (const r of rows) mountEl.appendChild(r);
    mountEl.appendChild(holdRow);
    mountEl.appendChild(btns);

    function say(msg) { state.textContent = msg; }

    // ── population (ONCE per load / per host-driven refresh) ────────────────
    // Never call this from a poll: a select that re-populates under the
    // operator's hand throws away a selection they are in the middle of making.
    async function load() {
      let payload;
      try { payload = await adapters.state(); } catch (e) { return false; }
      if (!payload || !payload.choices) return false;
      choices = payload;
      const current = payload.current || {};
      const chars = payload.choices.characters || [];
      fill(selects.character, chars.map((c) => c.name), current.character);
      fill(selects.model, payload.choices.models || [], current.model);
      dependents(true);

      const up = isUp(payload);
      go.textContent = up ? "Switch" : "Start";
      holdRow.style.display = up ? "" : "none";  // nothing to hold when down
      cur.textContent = current.character
        ? current.character + " · " + (current.voice || "?") + " · " +
          (current.model || "?")
        : "(no active.toml yet)";
      markResident();
      return true;
    }

    function isUp(payload) {
      const b = (payload && payload.bot) || {};
      return b.state === "running" || b.state === "starting";
    }

    function charInfo(name) {
      const chars = (choices && choices.choices && choices.choices.characters) || [];
      return chars.find((c) => c.name === name) || { voices: [], personas: [] };
    }

    // Voice + persona belong to the chosen character: re-derive on every change,
    // but keep the CURRENT values while the character is still the current one.
    function dependents(keepCurrent) {
      const name = selects.character.value;
      const current = (choices && choices.current) || {};
      const info = charInfo(name);
      const same = keepCurrent && name === current.character;
      fill(selects.voice, info.voices, same ? current.voice : info.voices[0]);
      fill(selects.persona, info.personas.length ? info.personas : ["default"],
           same ? (current.persona || "default") : "default");
    }

    selects.character.addEventListener("change", () => dependents(false));

    // ● = the LLM server holds it right now, so a model change can go live.
    // Absent marks are honest silence, never a claim that nothing is loaded.
    async function markResident() {
      if (!adapters.live) return;
      let info;
      try { info = await adapters.live(); } catch (e) { return; }
      if (!info || !info.ok || !info.resident_models) return;
      const ids = (choices.choices && choices.choices.model_ids) || {};
      for (const o of selects.model.options) {
        if (ids[o.value] && info.resident_models.includes(ids[o.value]))
          o.textContent = o.value + " ●";
      }
    }

    // ── submit ──────────────────────────────────────────────────────────────
    go.addEventListener("click", async () => {
      if (busy) return;
      const body = {};
      for (const k of SELECTION) body[k] = selects[k].value;
      if (hold.checked && holdRow.style.display !== "none") body.hold = true;
      Object.assign(body, (adapters.extraBody && adapters.extraBody()) || {});

      const wasUp = isUp(choices);
      busy = true;
      go.disabled = true;
      say(wasUp ? "switching…" : "starting…");
      let data = null;
      try {
        const r = await adapters.submit(body);
        data = r && r.data;
        if (!data || !data.ok) {
          const why = (data && (data.errors || [data.error])) || [r && r.status];
          say("refused: " + why.join(" · "));
          go.disabled = false;
          busy = false;
          return;
        }
      } catch (e) {
        // On the panel a restart can cut the reply off mid-flight; that is the
        // restart itself answering, not a failure.
        if (!adapters.selfDies) {
          say("switch failed: " + e.message);
          go.disabled = false;
          busy = false;
          return;
        }
      }
      if (data && data.applied === "live") return watchLive();
      watchRestart(wasUp);
    });

    // The handoff is ARMED on POST and lands at the next turn boundary — so the
    // honest report is "armed", and only the bot can say when it applied.
    function watchLive() {
      say("armed — applies the moment you next speak…");
      const t0 = Date.now();
      const poll = setInterval(async () => {
        let info = null;
        try { info = await adapters.live(); } catch (e) { return; }
        if (info && info.ok && !info.armed && info.last &&
            info.last.phase === "applied") {
          clearInterval(poll);
          say("switched ✓");
          done();
          return;
        }
        if (info && info.ok && info.last && info.last.phase === "failed") {
          clearInterval(poll);
          say("live apply failed — check the terminal / logs");
          go.disabled = false;
          busy = false;
          return;
        }
        if (Date.now() - t0 > 600000) {   // 10 min: it is armed, not stuck
          clearInterval(poll);
          say("still armed — it applies whenever you next speak");
          go.disabled = false;
          busy = false;
        }
      }, 2000);
    }

    // Restart: the panel goes down with the bot and must wait for the down→up
    // EDGE (reloading into a dying bot just shows a corpse); the facade page
    // survives and only needs to let its own status poll catch up.
    function watchRestart(wasUp) {
      if (!adapters.selfDies) {
        say(wasUp ? "restarting the voice bot — watch the state line"
                  : "start requested — watch the state line");
        go.disabled = false;
        busy = false;
        done();
        return;
      }
      say("restarting — this page comes back on its own (≈10–30 s)…");
      const t0 = Date.now();
      let wentDown = false;
      const poll = setInterval(async () => {
        try {
          const alive = await adapters.alive();
          if (alive && wentDown) { clearInterval(poll); done(); return; }
        } catch (e) { wentDown = true; }
        if (Date.now() - t0 > 120000) {
          clearInterval(poll);
          say("still down after 120 s — check the terminal / logs/bot.log");
          go.disabled = false;
          busy = false;
        }
      }, 2000);
    }

    function done() { if (adapters.onApplied) adapters.onApplied(); }

    return {
      load: load,
      busy: () => busy,
      character: () => selects.character.value,
      // The host hangs its own character-dependent extras off this (the launch
      // page's session list), rather than reaching into the card's selects.
      onCharacter: (cb) => selects.character.addEventListener("change", cb),
    };
  }

  return { mount: mount, SELECTION: SELECTION };
})();
