// settings_files.js — the config-file list.
// Spliced INSIDE settings_page.html's own <script> block
// (ui/settings_sections.py), so the admin shell's helpers and the page's
// own state are already in scope. Reads openFile() from settings_form.js.
//
// ── the file list ────────────────────────────────────────────────────────────

const VERDICT_MARK = { ok: "✓", warn: "~", INVALID: "✗", inert: "◌" };

function renderKinds() {
  const box = $("kinds");
  box.textContent = "";
  for (const k of overview.kinds || []) {
    const row = document.createElement("div"); row.className = "kind";
    const head = document.createElement("div"); head.className = "head";
    const title = document.createElement("span"); title.className = "title";
    title.textContent = k.title;
    const pat = document.createElement("span"); pat.className = "pat";
    pat.textContent = k.path;
    const own = document.createElement("span"); own.className = "badge";
    own.textContent = k.owner + " · " + k.layer;
    head.append(title, pat, own);
    row.appendChild(head);
    const files = document.createElement("div"); files.className = "row";
    for (const f of k.files || []) {
      const b = document.createElement("button");
      b.className = "filebtn" + (current && current.file === f.file ? " sel" : "");
      b.textContent = (VERDICT_MARK[f.verdict] || "?") + " " + f.file;
      if (f.verdict === "INVALID") b.classList.add("err");
      b.addEventListener("click", () => openFile(f.file));
      files.appendChild(b);
    }
    if (!(k.files || []).length) {
      const none = document.createElement("small");
      none.textContent = "(absent — nothing to show)";
      files.appendChild(none);
    }
    row.appendChild(files);
    box.appendChild(row);
  }
}

async function refresh() {
  if (!token()) { needToken(); return; }
  try {
    if (!schemas) {
      const s = await api("/admin/settings/schema");
      schemas = (s.data || {}).schema || {};
    }
    const o = await api("/admin/settings");
    overview = o.data || { kinds: [] };
  } catch (e) {
    if (e.message === "401") { needToken("that token was refused — try again"); return; }
    $("statusline").textContent = "facade unreachable — retrying…";
    return;
  }
  show("tokencard", false); show("filescard", true);
  renderKinds();
  const n = (overview.kinds || []).reduce((a, k) => a + (k.files || []).length, 0);
  $("statusline").textContent = n + " config file(s) discovered";
}
