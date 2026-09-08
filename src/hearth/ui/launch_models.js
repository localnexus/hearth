// launch_models.js — the launch page's "Models" card: enroll → render → apply
// → load, in the order the design names them.
//
// One row per enrolled model: its name, whether the file is still where it was
// (✓ present · ✗ MISSING · ⚠ changed — guard rail R3, a state and never a
// silent substitution), whether it fits this machine, ● when the door is
// holding it right now, and whether the launchd unit on disk is what this
// config renders to (applied / stale / unapplied). Then the three verbs, each
// of which SHOWS what it would do and waits for a second press: Render draws
// the argv and the classified diff, Apply draws the diff plus the archive it
// would make, Unenroll draws the reference it would drop.
//
// The footer's Load / Unload press the built-in `door-load` / `door-unload`
// actuators through the ordinary actuator runner — the same bounded,
// logged, never-a-child path the Externals card uses, and the same companion
// guard: while a companion is up, taking the door down is a cost the next turn
// pays, so it takes a confirmed press.
//
// "Enroll from disk…" runs the scan. That walk is minutes on a real model
// library, so the button says so and the request simply holds.
//
// Host supplies the authed api() and report(). Nothing here is drawn from
// markup: every node is built, so API text never meets innerHTML.
window.LaunchModels = (function () {
  const busy = new Set();          // model names (and "scan") with work in flight
  let scanOpen = false;
  let targets = [];
  let doorFacts = null;
  let companionUp = false;

  function mk(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  function gb(n) { return (Number(n || 0) / 1e9).toFixed(1) + " GB"; }

  const GLYPH = { present: "✓", missing: "✗", changed: "⚠" };

  function stateOf(m) {
    const bits = [(GLYPH[m.state] || "?") + " " + m.state];
    if (m.fit) bits.push(m.fit + (m.fit_at_ctx ? " @ " + m.fit_at_ctx : ""));
    if (m.resident === true) bits.push("● resident");
    else if (m.resident === false) bits.push("○ not loaded");
    bits.push("unit: " + m.unit +
              (m.diff && m.diff.real ? " (" + m.diff.real + " real)" : ""));
    return bits.join("  ·  ");
  }

  // ── the preview pane: what a verb WOULD do, then the confirming press ─────

  function diffTable(rows) {
    const box = mk("div", "state");
    for (const row of rows || []) {
      box.appendChild(mk("div", "", row.kind + "  " + row.flag +
                         "  ·  rendered " + row.rendered + "  ·  on disk " + row.live));
    }
    return box;
  }

  function argvBlock(argv) {
    const box = mk("div", "state");
    for (const token of argv || []) box.appendChild(mk("div", "", "  " + token));
    return box;
  }

  function clear(pane) { while (pane.firstChild) pane.removeChild(pane.firstChild); }

  // Draw a preview and offer the confirming press. `draw` fills the pane from
  // the preview body; `onDone` runs after a confirmed call answers.
  function offer(api, report, pane, url, body, draw, onDone) {
    return async function () {
      const key = body.model || "scan";
      if (busy.has(key)) return;
      busy.add(key);
      clear(pane);
      pane.appendChild(mk("div", "note", "reading…"));
      let r;
      try { r = await api(url, { json: body }); }
      catch (e) {
        busy.delete(key);
        report(e.message === "401" ? "key refused" : "failed: " + e.message, true);
        clear(pane);
        return;
      }
      busy.delete(key);
      clear(pane);
      const d = r.data || {};
      if (r.status !== 200 || !d.ok) {
        pane.appendChild(mk("div", "err", d.error || ("refused (" + r.status + ")")));
        if (d.guard === "companion") report(d.error, true);
        return;
      }
      draw(pane, d);
      if (d.warning) pane.appendChild(mk("div", "err", "note: " + d.warning));
      if (!d.confirm) { if (onDone) onDone(); return; }
      pane.appendChild(mk("div", "note", d.confirm));
      const go = mk("button", "primary", "Confirm");
      const no = mk("button", "", "Cancel");
      const row = mk("div", "row");
      row.appendChild(go); row.appendChild(no);
      pane.appendChild(row);
      no.addEventListener("click", () => clear(pane));
      go.addEventListener("click", async () => {
        go.disabled = true; no.disabled = true;
        busy.add(key);
        let done;
        try { done = await api(url, { json: Object.assign({}, body, { yes: true }) }); }
        catch (e) {
          busy.delete(key);
          report(e.message === "401" ? "key refused" : "failed: " + e.message, true);
          return;
        }
        busy.delete(key);
        const dd = done.data || {};
        clear(pane);
        if (done.status !== 200 || !dd.ok) {
          pane.appendChild(mk("div", "err", dd.error || ("refused (" + done.status + ")")));
          report(dd.error || "refused", true);
          return;
        }
        draw(pane, dd);
        if (dd.warning) pane.appendChild(mk("div", "err", "note: " + dd.warning));
        report("done");
        if (onDone) onDone();
      });
    };
  }

  function drawRender(pane, d) {
    pane.appendChild(mk("div", "note", "unit " + d.unit + " — " +
                        (d.diff.real || 0) + " real · " + (d.diff.placement || 0) +
                        " placement · " + (d.diff["deprecated-form"] || 0) +
                        " deprecated-form"));
    pane.appendChild(argvBlock(d.argv));
    pane.appendChild(diffTable(d.rows));
    if (d.wrote) pane.appendChild(mk("div", "note", "written to " + d.wrote));
    if (d.note) pane.appendChild(mk("div", "note", d.note));
  }

  function drawApply(pane, d) {
    const p = d.preview || {};
    pane.appendChild(mk("div", "note", "unit " + p.unit + " → " + p.target));
    pane.appendChild(diffTable(p.rows));
    if (d.applied) {
      if (d.archived) pane.appendChild(mk("div", "note", "archived " + d.archived));
      pane.appendChild(mk("div", "note", "wrote " + d.wrote));
      pane.appendChild(mk("div", "note", d.note || ""));
      for (const line of d.lines || []) pane.appendChild(mk("div", "state", line));
    } else if (p.archives) {
      pane.appendChild(mk("div", "note", "would archive as " + p.archives));
    }
  }

  function drawUnenroll(pane, d) {
    const p = d.preview || {};
    pane.appendChild(mk("div", "note", "drops the reference to " + p.weights));
    pane.appendChild(mk("div", "note", p.keeps || ""));
    if (d.unenrolled) pane.appendChild(mk("div", "note", "wrote " + d.wrote));
  }

  function drawEnroll(pane, d) {
    const p = d.preview || {};
    if (p.creates) pane.appendChild(mk("div", "note", "creates " + p.creates));
    pane.appendChild(mk("div", "note", p.weights.display_key + "  ·  " +
                        gb(p.weights.size_bytes) + "  ·  " + p.fit));
    pane.appendChild(mk("div", "note", "into " + p.writes));
    pane.appendChild(argvBlock((p.block || "").split("\n")));
    if (d.enrolled) {
      pane.appendChild(mk("div", "note", "wrote " + d.wrote));
      if (d.note) pane.appendChild(mk("div", "note", d.note));
    }
  }

  // ── the built-in door actuators, through the ordinary runner ──────────────

  async function press(api, report, name, btn, force) {
    busy.add(name);
    btn.disabled = true;
    const was = btn.textContent;
    btn.textContent = "working…";
    report(name + " — this holds until the command finishes");
    let again = false;
    try {
      const r = await api("/admin/actuators/" + encodeURIComponent(name) + "/run"
                          + (force ? "?force=1" : ""), { method: "POST" });
      const d = r.data || {};
      if (r.status === 409 && d.guard === "companion") {
        again = window.confirm(
          "A companion is running. " + name + " bounces the door underneath it — "
          + "the next turn would meet a model server mid-restart.\n\nDo it anyway?");
        if (!again) report(name + " held — a companion is running", true);
      } else if (r.status === 409) report(name + " is already running", true);
      else if (r.status === 404) report("no built-in " + name +
                                        " — config/weights.toml declares no [weights.door]", true);
      else if (d.ok) report(name + " ok in " + d.duration_s + "s");
      else report(name + " failed: " + (d.timed_out ? "timed out"
                  : d.exit === null || d.exit === undefined ? "could not start"
                  : "exit " + d.exit) + " — the log is on the machine", true);
    } catch (e) {
      report(e.message === "401" ? "key refused" : name + " failed: " + e.message, true);
    } finally {
      busy.delete(name);
      btn.textContent = was; btn.disabled = false;
      if (again) press(api, report, name, btn, true);
    }
  }

  // ── the scan list ────────────────────────────────────────────────────────

  async function loadScan(api, report, refresh) {
    const host = document.getElementById("modelscan");
    if (!host) return;
    clear(host);
    host.classList.remove("hidden");
    host.appendChild(mk("div", "note",
      "walking every root — on a real model library this takes a minute; "
      + "the wait is the scan."));
    busy.add("scan");
    let r;
    try { r = await api("/admin/models/scan" + (refresh ? "?refresh=1" : "")); }
    catch (e) {
      busy.delete("scan");
      clear(host);
      host.appendChild(mk("div", "err",
        e.message === "401" ? "key refused" : "scan failed: " + e.message));
      return;
    }
    busy.delete("scan");
    clear(host);
    const d = r.data || {};
    if (r.status !== 200) {
      host.appendChild(mk("div", "err", d.error || ("scan refused (" + r.status + ")")));
      return;
    }
    targets = d.targets || targets;
    const head = mk("div", "row");
    head.appendChild(mk("div", "grow", (d.candidates || []).length +
                        " candidate(s) in " + (d.roots || []).length + " root(s)" +
                        (d.cached ? " · from the last scan" : "") +
                        (d.scanned ? " · " + d.scanned.slice(0, 16).replace("T", " ") : "")));
    const again = mk("button", "", "Rescan");
    again.addEventListener("click", () => loadScan(api, report, true));
    head.appendChild(again);
    host.appendChild(head);
    for (const c of d.candidates || []) host.appendChild(candidateRow(api, report, c));
    // Symlinks that leave the roots are named and never followed (the scan
    // fence): the note carries the target, so adding it as a root is one edit.
    for (const e of d.elsewhere || []) {
      const row = mk("div", "row note");
      row.appendChild(mk("span", "grow", e.display_key + " — not followed: " + e.note));
      host.appendChild(row);
    }
  }

  function candidateRow(api, report, c) {
    const wrap = mk("div", "card");
    const line = mk("div", "grow", c.display_key);
    line.appendChild(mk("div", "state", gb(c.size_bytes) + "  ·  " +
      (c.architecture || "?") + "  ·  " + c.fit +
      (c.shards ? "  ·  " + c.shards + " shards" : "") +
      (c.duplicates.length ? "  ·  dup" : "") +
      (c.enrolled_as ? "  ·  enrolled as " + c.enrolled_as : "")));
    const top = mk("div", "row");
    top.appendChild(line);
    wrap.appendChild(top);

    const pick = document.createElement("select");
    for (const t of targets) {
      const o = document.createElement("option");
      o.value = t; o.textContent = t;
      pick.appendChild(o);
    }
    const fresh = document.createElement("option");
    fresh.value = "__new__"; fresh.textContent = "new…";
    pick.appendChild(fresh);
    const name = document.createElement("input");
    name.type = "text";
    name.placeholder = "new model directory name";
    name.classList.add("hidden");
    pick.addEventListener("change", () =>
      name.classList.toggle("hidden", pick.value !== "__new__"));
    const pane = mk("div");
    const go = mk("button", "", "Enroll");
    const row = mk("div", "row");
    row.appendChild(mk("label", "", "into"));
    row.appendChild(pick); row.appendChild(name); row.appendChild(go);
    wrap.appendChild(row);
    wrap.appendChild(pane);
    go.addEventListener("click", () => {
      const body = { identity: c.identity };
      if (pick.value === "__new__") body.new = (name.value || "").trim();
      else body.model = pick.value;
      if (!body.model && !body.new) { report("name the model directory first", true); return; }
      offer(api, report, pane, "/admin/models/enroll", body, drawEnroll, null)();
    });
    return wrap;
  }

  // ── the card ─────────────────────────────────────────────────────────────

  function modelRow(api, report, m) {
    const wrap = mk("div", "card");
    const left = mk("div", "grow", m.name);
    left.appendChild(mk("div", "state", stateOf(m)));
    if (m.weights.display_key)
      left.appendChild(mk("div", "note", m.weights.display_key + "  ·  " +
                          gb(m.weights.size_bytes)));
    if (m.state !== "present" && m.state_text)
      left.appendChild(mk("div", "err", m.state_text));
    const top = mk("div", "row");
    top.appendChild(left);
    wrap.appendChild(top);

    const pane = mk("div");
    const render = mk("button", "", "Render");
    const apply = mk("button", "", "Apply");
    const drop = mk("button", "", "Unenroll");
    // R3: a unit that points at a file which is not there any more would give
    // a door that cannot start. Look at it, yes; put it in place, no.
    if (m.state === "missing") {
      apply.disabled = true;
      apply.title = "the weights this unit names are not on disk — re-enroll first";
    }
    render.addEventListener("click",
      offer(api, report, pane, "/admin/models/render", { model: m.name },
            drawRender, null));
    apply.addEventListener("click",
      offer(api, report, pane, "/admin/models/apply", { model: m.name },
            drawApply, null));
    drop.addEventListener("click",
      offer(api, report, pane, "/admin/models/unenroll", { model: m.name },
            drawUnenroll, null));
    const buttons = mk("div", "row");
    buttons.appendChild(render); buttons.appendChild(apply); buttons.appendChild(drop);
    wrap.appendChild(buttons);
    wrap.appendChild(pane);
    return wrap;
  }

  function footer(api, report) {
    const host = document.getElementById("modelsdoor");
    if (!host) return;
    clear(host);
    if (!doorFacts || !doorFacts.declared) {
      host.appendChild(mk("div", "note",
        "no [weights.door] in config/weights.toml — nothing here can render or "
        + "load a unit yet."));
      return;
    }
    const line = mk("div", "grow", "door " + doorFacts.label + " · " +
      doorFacts.host + ":" + doorFacts.port + " · access key " + doorFacts.api_key +
      (doorFacts.load_mode ? " · load " + doorFacts.load_mode : ""));
    const load = mk("button", "", "Load");
    const unload = mk("button", "", "Unload");
    load.disabled = unload.disabled = companionUp;
    if (companionUp) load.title = unload.title =
      "a companion is up — stop it first, or confirm the press";
    load.addEventListener("click", () =>
      press(api, report, doorFacts.actuators.load, load, false));
    unload.addEventListener("click", () =>
      press(api, report, doorFacts.actuators.unload, unload, false));
    const scan = mk("button", "", "Enroll from disk…");
    scan.addEventListener("click", () => {
      scanOpen = !scanOpen;
      const pane = document.getElementById("modelscan");
      if (!scanOpen) { clear(pane); pane.classList.add("hidden"); return; }
      loadScan(api, report, false);
    });
    host.appendChild(line);
    const row = mk("div", "row");
    row.appendChild(load); row.appendChild(unload); row.appendChild(scan);
    host.appendChild(row);
  }

  async function refresh(api, report, up) {
    if (busy.size) return;    // never re-render under an in-flight verb
    companionUp = !!up;
    let r;
    try { r = await api("/admin/models"); } catch { return; }
    const d = (r && r.data) || {};
    const card = document.getElementById("modelscard");
    const models = d.models || [];
    doorFacts = d.door || null;
    targets = d.targets || [];
    const show = models.length > 0 || (doorFacts && doorFacts.declared);
    if (card) card.classList.toggle("hidden", !show);
    if (!show) return;
    const host = document.getElementById("models");
    if (!host) return;
    clear(host);
    if (!models.length)
      host.appendChild(mk("div", "note",
        "nothing enrolled yet — Enroll from disk… finds what is on this machine."));
    for (const m of models) host.appendChild(modelRow(api, report, m));
    footer(api, report);
  }

  return { refresh };
})();
