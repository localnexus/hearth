// roster_edit.js — the persona editor and add-a-voice.
// Spliced INSIDE roster_page.html's own <script> block
// (ui/roster_sections.py), so the admin shell's helpers and the page's
// own state are already in scope. Declares `roster`, the character list the other two sections read.
//
// ── persona editor + add-a-voice (the editing half) ─────────────────────────
let roster = [];  // /admin/roster/state characters, kept for the pickers

function fillCharPickers() {
  for (const id of ["e-char", "v-char", "b-char"]) {
    const sel = $(id), prev = sel.value;
    sel.textContent = "";
    for (const c of roster) {
      const o = document.createElement("option");
      o.value = c.name; o.textContent = c.name;
      sel.appendChild(o);
    }
    if (prev && roster.some(c => c.name === prev)) sel.value = prev;
  }
  fillVariantPicker();
}

function fillVariantPicker() {
  const c = roster.find(x => x.name === $("e-char").value);
  const sel = $("e-variant"), prev = sel.value;
  sel.textContent = "";
  for (const p of (c ? c.personas : ["default"])) {
    const o = document.createElement("option");
    o.value = p; o.textContent = p;
    sel.appendChild(o);
  }
  const nv = document.createElement("option");
  nv.value = "__new__"; nv.textContent = "new variant…";
  sel.appendChild(nv);
  if (prev && [...sel.options].some(o => o.value === prev)) sel.value = prev;
  show("e-newvariant", sel.value === "__new__");
}

function editVariant() {
  const v = $("e-variant").value;
  return v === "__new__" ? $("e-newvariant").value.trim() : v;
}

function lockSave() { $("e-save").disabled = true; }

$("e-char").addEventListener("change", () => { fillVariantPicker(); lockSave(); });
$("e-variant").addEventListener("change", () => {
  show("e-newvariant", $("e-variant").value === "__new__"); lockSave();
});
$("e-newvariant").addEventListener("input", lockSave);
$("e-text").addEventListener("input", lockSave);

$("e-load").addEventListener("click", async () => {
  report("");
  const character = $("e-char").value, variant = editVariant();
  if ($("e-variant").value === "__new__") {
    // A new variant starts from the scaffold — nothing to load.
    $("e-text").value = "## IDENTITY\n\n\n\n## SOUL\n\n\n";
    show("e-text", true); show("e-preview", true); show("e-save", true); lockSave();
    report(variant ? "new variant “" + variant + "” — write it, then Preview"
                   : "name the new variant first", !variant);
    return;
  }
  let r;
  try { r = await api("/admin/roster/persona?character=" + encodeURIComponent(character) +
                      "&persona=" + encodeURIComponent(variant)); }
  catch (e) {
    if (e.message === "401") { needToken("that key was refused — try again"); return; }
    report("Hearth is not answering", true); return;
  }
  const d = r.data || {};
  if (r.status !== 200) { report(d.error || "load failed", true); return; }
  $("e-text").value = d.text || "";
  show("e-text", true); show("e-preview", true); show("e-save", true); lockSave();
  report(d.editable_note || "");
});

async function personaSubmit(confirmed) {
  report(confirmed ? "saving…" : "checking…");
  lockSave();
  const body = { character: $("e-char").value, persona: editVariant(),
                 text: $("e-text").value };
  if (confirmed) body.yes = true;
  let r;
  try {
    r = await api("/admin/roster/persona", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (e) {
    if (e.message === "401") { needToken("that key was refused — try again"); return; }
    report("Hearth is not answering", true); return;
  }
  const d = r.data || {};
  if (d.errors) { report(d.errors.map(x => "✗ " + x).join("\n"), true); return; }
  if (!confirmed) {
    report((d.action || "write") + " → " + (d.target || "") + " (" + d.chars +
           " chars)\n" + (d.confirm || ""));
    $("e-save").disabled = !d.ok;
    return;
  }
  report((d.action || "written") + " → " + (d.target || "") +
         (d.backup ? "\nbackup: " + d.backup : "") +
         "\n" + (d.effect || ""), !d.ok);
  if (d.written) refresh();
}

$("e-preview").addEventListener("click", () => personaSubmit(false));
$("e-save").addEventListener("click", () => personaSubmit(true));

function voiceForm(confirmed) {
  const fd = new FormData();
  fd.append("character", $("v-char").value);
  fd.append("voice_tag", $("v-tag").value.trim());
  fd.append("license", $("v-license").value);
  fd.append("source", $("v-source").value.trim());
  if (confirmed) fd.append("yes", "true");
  const file = $("v-sample").files[0];
  if (file) fd.append("sample", file, file.name);
  return fd;
}

async function voiceSubmit(confirmed) {
  report(confirmed ? "adding…" : "checking…");
  $("v-add").disabled = true;
  let r;
  try { r = await api("/admin/roster/voice", { method: "POST", body: voiceForm(confirmed) }); }
  catch (e) {
    if (e.message === "401") { needToken("that key was refused — try again"); return; }
    report("Hearth is not answering", true); return;
  }
  const d = r.data || {};
  report(renderReport(d), !d.ok);
  $("v-add").disabled = !(d.ok && !d.created);
  if (d.created) { $("v-add").disabled = true; refresh(); }
}

$("v-preview").addEventListener("click", () => voiceSubmit(false));
$("v-add").addEventListener("click", () => voiceSubmit(true));
for (const id of ["v-char", "v-tag", "v-license", "v-source", "v-sample"])
  $(id).addEventListener("input", () => { $("v-add").disabled = true; });
