// launch_sessions.js — the launch page's "Sessions" card: the web half of the
// five session-file verbs (reveal · download · deposit · archive · destroy ·
// rename) that `supervisor/routes/sessions.py` grew one stroke at a time.
//
// One row per saved conversation of the companion the page has selected: the
// word it goes by (its title, else the name it was held under, else its id —
// exactly destroy's own precedence, so the word the person is asked to type
// back is the word they were shown), how many turns, when it was last written,
// its memory posture, whether it is held, whether it was brought in, and
// whether it is archived. Then the verbs it is actually offered.
//
// WHAT IS OFFERED IS PROBED, NEVER GUESSED. The panel has no audience layer in
// code — one access key, one caller — so the card learns what this browser may
// do from the routes' own refusals and then stops asking:
//
//   · Reveal opens the file manager on the machine Hearth runs on. From a phone
//     it answers 409 ("use download instead") and off macOS 501. Either one
//     retires the verb for the life of the page — remembered in a variable
//     here, deliberately not in storage: it is a fact about where this browser
//     is sitting right now, not a preference to carry into the next visit.
//   · Destroy answers 403 where it is not offered (the irreversible verb stays
//     off the phone unless [serve.sessions] destroy_for_all says otherwise).
//     The refusal is shown ONCE and the button leaves every row.
//   · Every mutating verb answers 409 with the live-session guard while the
//     companion is up. One banner carries it for the whole card, because the
//     guard IS the whole shelf.
//
// A recall-only sitting is transcript-ephemeral and is the privacy tier's own
// case: it is not offered load or rename, so the only verb on its row is
// Destroy.
//
// Download cannot be a plain link: a navigation carries no Authorization
// header, and the bearer is never a query parameter. So it is an authed fetch
// to /admin/sessions/file, the answer to a blob, an object URL clicked once and
// revoked — the host hands in `authFetch` (fetch + the bearer header) for this
// and for the multipart deposit, both of which want the raw response rather
// than api()'s parsed JSON.
//
// Nothing here renders a path and nothing renders a line of a conversation —
// the routes never send either, and this is the half that must not invent them.
// Every node is built, so API text never meets innerHTML.
window.LaunchSessions = (function () {
  // These three sentences are the ROUTES' words, kept here only as the fallback
  // for an answer that arrived without one. The refusal a person reads is the
  // one the route sent; test_launch_sessions.py holds these to the same text so
  // the fallback cannot drift into a second, softer story.
  const GUARD_TAIL = " is running — stop the companion first; its session " +
                     "files are read-only while it is up";
  const DESTROY_NOT_OFFERED =
    "destroy is not offered here — enable [serve.sessions] destroy_for_all, " +
    "or use the panel on the machine Hearth runs on";
  const REFERENCED = "this session is referenced — rename its title instead";

  let revealOffered = true;    // until a 409/501 says this browser is elsewhere
  let destroyOffered = true;   // until a 403 says this browser may not
  let destroySaid = false;     // the 403's own words, shown once
  let showArchived = false;
  let panesOpen = 0;           // a destroy confirm freezes the reload under it
  let ctx = null;              // the last refresh's arguments, for a re-render

  function mk(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  function clear(node) { while (node && node.firstChild) node.removeChild(node.firstChild); }

  // The word this conversation goes by — destroy's precedence, so the confirm
  // asks for something the shelf is showing.
  function wordOf(s) {
    return (s.title || "").trim() || (s.name || "").trim() || s.session_id;
  }

  function stamp(s) {
    const when = s.updated || s.started || "";
    return when ? String(when).slice(0, 16).replace("T", " ") : "—";
  }

  function flagsOf(s) {
    const bits = [];
    if (s.held) bits.push("held");
    if (s.archived) bits.push("archived");
    if (s.origin) bits.push("brought in");
    return bits.join(" · ");
  }

  function errorOf(r, fallback) {
    const d = (r && r.data) || {};
    return d.error || fallback || ("refused (" + ((r && r.status) || "?") + ")");
  }

  // ── the download, which a plain <a href> could never do ──────────────────

  async function download(s) {
    const q = "/admin/sessions/file?character=" + encodeURIComponent(ctx.character) +
              "&session=" + encodeURIComponent(s.session_id) +
              (s.archived ? "&archived=1" : "");
    let resp;
    try { resp = await ctx.authFetch(q); }
    catch (e) { ctx.report("download failed: " + e.message, true); return; }
    if (!resp.ok) {
      let msg = "download refused (" + resp.status + ")";
      try { const d = await resp.json(); if (d && d.error) msg = d.error; } catch { /* not JSON */ }
      ctx.report(msg, true);
      return;
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = ctx.character + "-" + s.session_id + ".json";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
    ctx.report("downloaded " + wordOf(s));
  }

  // ── the verbs that are one POST and a re-render ──────────────────────────

  async function post(url, body, pane, okText) {
    let r;
    try { r = await ctx.api(url, { json: body }); }
    catch (e) {
      ctx.report(e.message === "401" ? "key refused" : "failed: " + e.message, true);
      return null;
    }
    const d = r.data || {};
    if (r.status !== 200 || !d.ok) {
      const msg = errorOf(r, null);
      if (pane) { clear(pane); pane.appendChild(mk("div", "err", msg)); }
      ctx.report(msg, true);
      return r;
    }
    if (okText) ctx.report(okText);
    await reload();
    return r;
  }

  async function reveal(s, pane) {
    let r;
    try {
      r = await ctx.api("/admin/sessions/reveal",
                        { json: { character: ctx.character, session: s.session_id,
                                  archived: !!s.archived } });
    } catch (e) {
      ctx.report(e.message === "401" ? "key refused" : "reveal failed: " + e.message, true);
      return;
    }
    const d = r.data || {};
    if (r.status === 409 || r.status === 501) {
      // Not a failure — an answer about where this browser is. Take the verb
      // away rather than offering something that cannot work here again.
      revealOffered = false;
      ctx.report(errorOf(r, "reveal only works on the machine Hearth runs on"), true);
      await reload();
      return;
    }
    if (r.status !== 200 || !d.ok) {
      clear(pane);
      pane.appendChild(mk("div", "err", errorOf(r, null)));
      return;
    }
    ctx.report("shown in the file manager");
  }

  function renameTitle(s, pane) {
    const now = (s.title || "").trim();
    const next = window.prompt(
      "A title for this conversation — the name the shelf shows. " +
      "Empty removes it.", now);
    if (next === null) return;
    post("/admin/sessions/rename",
         { character: ctx.character, session: s.session_id,
           archived: !!s.archived, title: next },
         pane, next.trim() ? "titled " + next.trim() : "title removed");
  }

  async function renameId(s, pane) {
    const next = window.prompt(
      "A new id for this conversation. The id is the file's name and the key " +
      "other parts of Hearth file things under, so this is only offered when " +
      "nothing knows it.", s.session_id);
    if (next === null) return;
    let r;
    try {
      r = await ctx.api("/admin/sessions/rename",
                        { json: { character: ctx.character, session: s.session_id,
                                  archived: !!s.archived, new_id: next } });
    } catch (e) {
      ctx.report(e.message === "401" ? "key refused" : "rename failed: " + e.message, true);
      return;
    }
    const d = r.data || {};
    if (r.status === 409 && d.references) {
      // The refusal IS the answer: what knows this id, in the route's words.
      clear(pane);
      pane.appendChild(mk("div", "err", d.error || REFERENCED));
      const list = mk("div", "state");
      for (const ref of d.references) list.appendChild(mk("div", "", "· " + ref));
      pane.appendChild(list);
      ctx.report(d.error || REFERENCED, true);
      return;
    }
    if (r.status !== 200 || !d.ok) {
      clear(pane);
      pane.appendChild(mk("div", "err", errorOf(r, null)));
      ctx.report(errorOf(r, null), true);
      return;
    }
    ctx.report("id is now " + d.session_id);
    await reload();
  }

  // ── destroy: the plan, the word, the second press ────────────────────────

  async function destroy(s, pane) {
    const body = { character: ctx.character, session: s.session_id,
                   archived: !!s.archived };
    let r;
    try { r = await ctx.api("/admin/sessions/destroy", { json: body }); }
    catch (e) {
      ctx.report(e.message === "401" ? "key refused" : "destroy failed: " + e.message, true);
      return;
    }
    const d = r.data || {};
    if (r.status === 403) {
      destroyOffered = false;
      if (!destroySaid) {
        destroySaid = true;
        ctx.report(errorOf(r, DESTROY_NOT_OFFERED), true);
      }
      await reload();
      return;
    }
    if (r.status !== 200 || !d.ok) {
      clear(pane);
      pane.appendChild(mk("div", "err", errorOf(r, null)));
      ctx.report(errorOf(r, null), true);
      return;
    }
    drawPlan(s, pane, d);
  }

  function drawPlan(s, pane, d) {
    clear(pane);
    panesOpen += 1;
    const plan = d.plan || {};
    const mem = plan.memory || {};
    pane.appendChild(mk("div", "err", d.warning || ""));
    const goes = mk("div", "state");
    goes.appendChild(mk("div", "", (plan.file ? "· the session file" :
                                    "· no file left — the records below only")));
    goes.appendChild(mk("div", "", "· " + (mem.records || 0) +
      " memory record(s)" + (mem.backend ? ", and the facts indexed from them" : "")));
    pane.appendChild(goes);
    pane.appendChild(mk("div", "note", "what destroy cannot reach:"));
    const cannot = mk("div", "state");
    for (const line of d.cannot_reach || plan.cannot_reach || [])
      cannot.appendChild(mk("div", "", "· " + line));
    pane.appendChild(cannot);

    const field = document.createElement("input");
    field.type = "text";
    field.autocomplete = "off";
    field.placeholder = "type " + d.confirm_with + " to confirm";
    const go = mk("button", "danger", "Destroy");
    const no = mk("button", "", "Cancel");
    const row = mk("div", "row");
    row.appendChild(field); row.appendChild(go); row.appendChild(no);
    pane.appendChild(row);
    no.addEventListener("click", () => { panesOpen = Math.max(0, panesOpen - 1); clear(pane); });
    go.addEventListener("click", async () => {
      if ((field.value || "") !== d.confirm_with) {
        ctx.report("that is not the word this conversation goes by", true);
        return;
      }
      go.disabled = no.disabled = true;
      panesOpen = Math.max(0, panesOpen - 1);
      const body = { character: ctx.character, session: s.session_id,
                     archived: !!s.archived, confirm: field.value };
      await post("/admin/sessions/destroy", body, pane, "destroyed " + wordOf(s));
    });
  }

  // ── bringing a file in ───────────────────────────────────────────────────

  function deposit(host) {
    clear(host);
    const field = document.createElement("input");
    field.type = "file";
    field.accept = "application/json,.json";
    const go = mk("button", "", "Bring in");
    const row = mk("div", "row");
    row.appendChild(field); row.appendChild(go);
    const pane = mk("div");
    host.appendChild(mk("div", "note",
      "A session file — one downloaded from here, or from another install. It " +
      "joins this companion's shelf under a fresh id and never replaces one."));
    host.appendChild(row);
    host.appendChild(pane);
    go.addEventListener("click", async () => {
      const file = (field.files || [])[0];
      if (!file) { ctx.report("pick a session file first", true); return; }
      const form = new FormData();
      form.append("character", ctx.character);
      form.append("file", file);
      go.disabled = true;
      clear(pane);
      pane.appendChild(mk("div", "note", "reading…"));
      let resp;
      try { resp = await ctx.authFetch("/admin/sessions/deposit",
                                       { method: "POST", body: form }); }
      catch (e) {
        go.disabled = false;
        clear(pane);
        pane.appendChild(mk("div", "err", "the file could not be sent: " + e.message));
        return;
      }
      go.disabled = false;
      let d = {};
      try { d = await resp.json(); } catch { /* non-JSON answer */ }
      clear(pane);
      if (!resp.ok || !d.ok) {
        // The route's own sentence about the FILE, verbatim — 400 and 413 both.
        pane.appendChild(mk("div", "err",
          d.error || ("the deposit was refused (" + resp.status + ")")));
        return;
      }
      field.value = "";
      pane.appendChild(mk("div", "note",
        "brought in as " + d.session_id + " · " + d.turns + " turns · " +
        d.dropped_system_messages + " prompt message(s) dropped"));
      ctx.report("brought in " + d.session_id);
      await reload();
    });
  }

  // ── the card ─────────────────────────────────────────────────────────────

  function row(s) {
    const tr = document.createElement("tr");
    const pane = mk("div");
    const cell = (text, cls) => {
      const td = document.createElement("td");
      if (cls) td.className = cls;
      if (text !== undefined) td.textContent = text;
      tr.appendChild(td);
      return td;
    };
    cell(wordOf(s));
    cell(String(s.turns == null ? "?" : s.turns), "state");
    cell(stamp(s), "state");
    cell(s.memory_mode || "full", "state");
    cell(flagsOf(s), "note");

    const verbs = document.createElement("td");
    const add = (label, cls, fn) => {
      const b = mk("button", cls, label);
      b.addEventListener("click", () => fn(s, pane));
      verbs.appendChild(b);
      return b;
    };
    // The privacy tier's own row: a transcript-ephemeral sitting is not offered
    // load or rename (§2), so destroy is the only verb it has.
    const ephemeral = s.memory_mode === "recall-only";
    if (!ephemeral) {
      if (revealOffered) add("Reveal", "", reveal);
      add("Download", "", (x) => download(x));
      add(s.archived ? "Unarchive" : "Archive", "",
          (x, p) => post(s.archived ? "/admin/sessions/unarchive"
                                    : "/admin/sessions/archive",
                         { character: ctx.character, session: x.session_id },
                         p, s.archived ? "back on the shelf" : "archived"));
      add("Rename", "", renameTitle);
      const idLink = mk("button", "linky", "change id…");
      idLink.addEventListener("click", () => renameId(s, pane));
      verbs.appendChild(idLink);
    }
    if (destroyOffered) add("Destroy", "danger", destroy);
    tr.appendChild(verbs);

    const paneRow = document.createElement("tr");
    const paneCell = document.createElement("td");
    paneCell.colSpan = 6;
    paneCell.appendChild(pane);
    paneRow.appendChild(paneCell);
    return [tr, paneRow];
  }

  function head() {
    const host = document.getElementById("sessionshead");
    if (!host) return;
    clear(host);
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = showArchived;
    box.id = "sessarchived";
    const label = mk("label", "", "");
    label.appendChild(box);
    label.appendChild(document.createTextNode(" show archived"));
    box.addEventListener("change", () => { showArchived = box.checked; reload(); });
    const line = mk("div", "row");
    line.appendChild(label);
    host.appendChild(line);
  }

  async function reload() {
    if (!ctx) return;
    await refresh(ctx.api, ctx.report, ctx);
    if (ctx.onChanged) await ctx.onChanged();   // the Conversation picker follows
  }

  async function refresh(api, report, opts) {
    const o = opts || {};
    const card = document.getElementById("sessionscard");
    const host = document.getElementById("sessions");
    if (!card || !host) return;
    ctx = { api, report, character: o.character, up: !!o.up,
            authFetch: o.authFetch, onChanged: o.onChanged };
    if (!ctx.character) { card.classList.add("hidden"); return; }
    if (panesOpen) return;      // never re-render out from under a confirm
    let r;
    try {
      r = await api("/admin/sessions?character=" + encodeURIComponent(ctx.character) +
                    (showArchived ? "&archived=all" : ""));
    } catch { card.classList.add("hidden"); return; }
    card.classList.remove("hidden");
    const note = document.getElementById("sessionsnote") || mk("div");
    clear(note);
    if (ctx.up) {
      // The whole shelf is read-only while its companion is up — say it once,
      // in the guard's own words, rather than once per refused press.
      note.appendChild(mk("div", "err", ctx.character + GUARD_TAIL));
    }
    if (!destroyOffered && destroySaid)
      note.appendChild(mk("div", "note", DESTROY_NOT_OFFERED));
    clear(host);
    const d = (r && r.data) || {};
    if (r && r.status !== 200) {
      host.appendChild(mk("div", "err", errorOf(r, null)));
      return;
    }
    head();
    const sessions = d.sessions || [];
    if (!sessions.length) {
      host.appendChild(mk("div", "note",
        "no saved conversations yet — one is written as soon as this companion "
        + "is talked to."));
    } else {
      const table = document.createElement("table");
      table.className = "sess";
      const hr = document.createElement("tr");
      for (const h of ["Conversation", "turns", "written", "memory", "", ""]) {
        const th = document.createElement("th");
        th.textContent = h;
        hr.appendChild(th);
      }
      table.appendChild(hr);
      for (const s of sessions) for (const node of row(s)) table.appendChild(node);
      host.appendChild(table);
    }
    deposit(document.getElementById("sessionsdeposit"));
  }

  return { refresh };
})();
