// firstrun_check.js — step 1: is the server answering, and which model.
// Spliced INSIDE first_run_page.html's own <script> block
// (ui/firstrun_sections.py) after the admin shell, so `$`, `el`, `api`,
// `show` and `report` are in scope. Declares `fr` and the page's refresh();
// firstrun_listen.js is handed each payload through renderListen() (a
// function declaration — it hoists) and never reads `fr` itself.
let fr = null;           // the last /admin/first-run/state payload
let modelBusy = false;   // a write in flight — never re-fill the picker under it

function needToken(prompt) {
  show("tokencard", true);
  for (const id of ["intro", "servercard", "startcard", "listencard", "donecard"])
    show(id, false);
  $("statusline").textContent = prompt || "locked — enter the bearer token";
}

async function refresh() {
  if (!token()) { needToken(); return; }
  let st;
  try { st = await api("/admin/first-run/state"); }
  catch (e) {
    if (e.message === "401") { needToken("that token was refused — try again"); return; }
    $("statusline").textContent = "facade unreachable — retrying…";
    return;
  }
  show("tokencard", false); show("intro", true); show("servercard", true);
  fr = st.data || {};
  renderServer(fr);
  // Step 2 stays parked until step 1 is done: a bot started against the
  // placeholder id would ask the server for a model that does not exist.
  const ready = !fr.needs_model;
  show("startcard", ready); show("listencard", ready);
  if (!ready) show("donecard", false);
  $("statusline").textContent = [
    ready ? "model id set ✓" : "model id: placeholder",
    (fr.lm && fr.lm.reachable) ? "server ✓" : "server ✗",
    "bot: " + ((fr.bot && fr.bot.state) || "?"),
  ].join("  ·  ");
  if (ready) await renderListen(fr);
}

function renderServer(d) {
  const lm = d.lm || {};
  const model = d.model || {};
  const ids = lm.models || [];
  $("lmline").textContent = lm.reachable
    ? "answering at " + lm.url + " — " + ids.length + " model" +
      (ids.length === 1 ? "" : "s") + " advertised"
    : "nothing answering at " + lm.url;
  show("lmhelp", lm.reachable === false);
  const name = "model config “" + (model.name || "?") + "”";
  $("modelline").textContent = model.id_set
    ? name + " → id " + model.id + " ✓"
    : name + " still carries the placeholder id" +
      (ids.length ? " — pick the one to serve:"
       : lm.reachable ? " — the server lists no models; load one there first" : "");
  // Re-fill the picker only when nothing is in flight and the list changed: a
  // select that re-populates under the hand throws the choice away.
  const sel = $("lm-model");
  const have = Array.from(sel.options).map((o) => o.value).join("\n");
  if (!modelBusy && have !== ids.join("\n")) {
    sel.replaceChildren();
    for (const id of ids) { const o = el("option", "", id); o.value = id; sel.appendChild(o); }
    if (model.id && ids.includes(model.id)) sel.value = model.id;
  }
  show("modelform", ids.length > 0);
}

async function useModel() {
  const id = $("lm-model").value;
  if (!id) { report("pick a model first", true); return; }
  modelBusy = true; report("recording the model id…");
  try {
    const r = await api("/admin/first-run/model", { json: { id } });
    const d = r.data || {};
    if (d.ok) report((d.written ? "recorded — " : "already set — ") + d.effect);
    else report("refused: " + (d.error || r.status), true);
  } catch (e) {
    report(e.message === "401" ? "token refused" : "failed: " + e.message, true);
  } finally { modelBusy = false; refresh(); }
}

$("modelbtn").addEventListener("click", useModel);
