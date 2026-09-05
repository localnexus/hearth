// settings_confirm.js — preview-then-confirm on a write.
// Spliced INSIDE settings_page.html's own <script> block
// (ui/settings_sections.py), so the admin shell's helpers and the page's
// own state are already in scope. Reads refresh() (files) and openFile()/fmtVal() (form).
//
// ── preview-then-confirm ─────────────────────────────────────────────────────

function clearPending() { pending = null; show("confirmcard", false); }

function coerce(s, raw) {
  if (s.type === "boolean") return raw === "true";
  if (s.type === "integer") { const n = parseInt(raw, 10); return Number.isNaN(n) ? null : n; }
  if (s.type === "number") { const n = parseFloat(raw); return Number.isNaN(n) ? null : n; }
  return raw;
}

async function askSet(key, s, raw) {
  report("");
  const value = coerce(s, raw);
  if (value === null) { report("not a valid number", true); return; }
  let r;
  try {
    r = await api("/admin/settings/set", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file: current.file, key, value }),
    });
  } catch (e) {
    if (e.message === "401") { needToken("that key was refused — try again"); return; }
    report("Hearth is not answering", true); return;
  }
  const d = r.data || {};
  if (r.status !== 200 || !d.ok) {
    report(d.error + (d.detail ? "\n" + d.detail.join("\n") : ""), true);
    return;
  }
  pending = { file: current.file, key, value };
  $("preview").textContent =
    current.file + "\n" + key + ": " + fmtVal(d.old) + "  →  " + fmtVal(d.new) +
    "\n\n" + effectText(d.effect || {});
  show("confirmcard", true);
}

async function confirmSet() {
  if (!pending) return;
  const { file, key, value } = pending;
  report("writing…");
  let r;
  try {
    r = await api("/admin/settings/set", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file, key, value, yes: true }),
    });
  } catch (e) {
    if (e.message === "401") { needToken("that key was refused — try again"); return; }
    report("Hearth is not answering", true); return;
  }
  clearPending();
  const d = r.data || {};
  if (r.status !== 200 || !d.ok) { report((d.error || "write failed") + " — nothing changed", true); }
  else {
    let line = "set " + key + " = " + fmtVal(d.new);
    if (d.target && d.target !== file) line += "\n" + d.target;
    if (d.backup) line += "\n(previous version kept as " + d.backup + ")";
    line += "\n" + effectText(d.effect || {});
    report(line);
  }
  await refresh();
  if (current) openFile(current.file);
}

$("confirmbtn").addEventListener("click", confirmSet);
$("cancelbtn").addEventListener("click", () => { clearPending(); report(""); });
