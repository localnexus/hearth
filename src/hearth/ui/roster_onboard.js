// roster_onboard.js — onboarding a new companion.
// Spliced INSIDE roster_page.html's own <script> block
// (ui/roster_sections.py), so the admin shell's helpers and the page's
// own state are already in scope. Reads `roster` and fillCharPickers() from roster_edit.js.
//
function needToken(prompt) {
  show("tokencard", true); show("rostercard", false); show("wizardcard", false);
  show("editcard", false); show("voicecard", false); show("branchcard", false);
  $("statusline").textContent = prompt || "locked — enter the bearer token";
}

async function refresh() {
  if (!token()) { needToken(); return; }
  let st;
  try { st = await api("/admin/roster/state"); }
  catch (e) {
    if (e.message === "401") { needToken("that token was refused — try again"); return; }
    $("statusline").textContent = "facade unreachable — retrying…";
    return;
  }
  show("tokencard", false); show("rostercard", true); show("wizardcard", true);
  show("editcard", true); show("voicecard", true); show("branchcard", true);
  const d = st.data || {};
  roster = d.characters || [];
  fillCharPickers();
  const lines = (d.characters || []).map(c => {
    const bits = [c.name];
    if (d.active && d.active.character === c.name) bits.push("● live");
    bits.push("voices: " + ((c.voices || []).join(", ") || "—"));
    if ((c.personas || []).length > 1) bits.push("personas: " + c.personas.join(", "));
    if (c.memory_backend) bits.push("memory: " + c.memory_backend);
    return "  " + bits.join("  ·  ");
  });
  $("roster").textContent = lines.join("\n") || "  (no characters found)";
  $("statusline").textContent = (d.characters || []).length + " companion(s)" +
    (d.memory_enabled ? "" : "  ·  memory not enabled");
  $("ffnote").textContent = d.ffmpeg
    ? "any audio format accepted — conditioned to mono 24 kHz automatically"
    : "ffmpeg not installed — upload an already-conforming WAV (mono, ~24 kHz)";
}

function formData(confirmed) {
  const fd = new FormData();
  fd.append("name", $("f-name").value.trim());
  fd.append("persona", $("f-persona").value);
  fd.append("voice_tag", $("f-tag").value.trim());
  fd.append("license", $("f-license").value);
  fd.append("source", $("f-source").value.trim());
  fd.append("memory_tier", $("f-tier").value);
  if (confirmed) fd.append("yes", "true");
  const file = $("f-sample").files[0];
  if (file) fd.append("sample", file, file.name);
  return fd;
}

function renderReport(d) {
  const out = [];
  if (d.errors) return d.errors.map(e => "✗ " + e).join("\n");
  if (d.clip) out.push(`clip: ${d.clip.duration_s}s · ${d.clip.channels}ch · ` +
                       `${d.clip.rate} Hz · ${d.clip.processing}`);
  if (d.memory) out.push("memory: " + d.memory);
  if (d.loader) out.push("loader: " + d.loader);
  if (d.files) out.push("written:\n  " + d.files.join("\n  "));
  if (d.confirm) out.push("\n" + d.confirm);
  if (d.next) out.push("\nNEXT: " + d.next);
  return out.join("\n");
}

async function submit(confirmed) {
  report(confirmed ? "creating…" : "checking…");
  $("createbtn").disabled = true;
  let r;
  try { r = await api("/admin/roster/onboard", { method: "POST", body: formData(confirmed) }); }
  catch (e) {
    if (e.message === "401") { needToken("that token was refused — try again"); return; }
    report("facade unreachable", true); return;
  }
  const d = r.data || {};
  report(renderReport(d), !d.ok);
  // Create unlocks only after a clean preview of the SAME form (edits re-lock).
  $("createbtn").disabled = !(d.ok && !d.created);
  if (d.created) { $("createbtn").disabled = true; refresh(); }
}

$("previewbtn").addEventListener("click", () => submit(false));
$("createbtn").addEventListener("click", () => submit(true));
for (const id of ["f-name", "f-persona", "f-tag", "f-license", "f-source", "f-tier", "f-sample"])
  $(id).addEventListener("input", () => { $("createbtn").disabled = true; });
