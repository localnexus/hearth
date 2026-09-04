// roster_fork.js — branching a character (the fork verb's web skin).
// Spliced INSIDE roster_page.html's own <script> block
// (ui/roster_sections.py), so the admin shell's helpers and the page's
// own state are already in scope. Reads `roster` from roster_edit.js; nothing reads it back.
//
// ── branch a character (the fork verb's web skin) ───────────────────────────
function lockFork() { $("b-fork").disabled = true; }

$("b-load").addEventListener("click", async () => {
  report("");
  const character = $("b-char").value;
  if (!character) { report("pick a source character first", true); return; }
  let r;
  try { r = await api("/admin/memory/records?character=" + encodeURIComponent(character)); }
  catch (e) {
    if (e.message === "401") { needToken("that token was refused — try again"); return; }
    report("facade unreachable", true); return;
  }
  const d = r.data || {};
  if (r.status !== 200) { report(d.error || "load failed", true); return; }
  const box = $("b-records");
  box.textContent = "";
  for (const rec of (d.records || [])) {
    const row = document.createElement("div");
    row.textContent = (rec.when || "(undated)") + "  " + rec.session_id +
                      (rec.name ? "  “" + rec.name + "”" : "");
    if (rec.ended) {
      row.title = "fork after this session (juncture = " + rec.ended + ")";
      row.addEventListener("click", () => {
        $("b-until").value = rec.ended;
        for (const el of box.children) el.classList.remove("picked");
        row.classList.add("picked");
        lockFork();
      });
    } else {
      row.style.opacity = "0.5";
      row.title = "undated — cannot anchor a juncture";
    }
    box.appendChild(row);
  }
  if (!box.children.length) box.textContent = "(no records — a fork from here starts amnesiac)";
  show("b-records", true);
});

function renderForkPlan(d) {
  if (d.errors) return d.errors.map(e => "✗ " + e).join("\n");
  const out = [`fork ${d.source} → ${d.target} at juncture ${d.juncture}`];
  const recs = d.records || [];
  out.push(recs.length
    ? `shared history — ${recs.length} record(s):\n` +
      recs.map(r => `  ${r.when}  ${r.session_id}` + (r.name ? `  “${r.name}”` : "")).join("\n")
    : "shared history — NO records at or before the juncture (starts amnesiac)");
  if (d.left_behind) out.push(`${d.left_behind} record(s) after the juncture stay behind`);
  if (d.undated) out.push(`${d.undated} undated record(s) stay behind`);
  out.push(`identity — ${d.identity_files} file(s) · voices: ` +
           ((d.voices || []).join(", ") || "none"));
  out.push("sessions — " + (d.sessions ? d.sessions + " transcript(s) copy over"
                                       : "stay with the source"));
  out.push(d.intent_note || "");
  out.push("memory tier — " + (d.tier === null ? "memory disabled, no enrollment" : d.tier));
  out.push(d.persona_note || "");
  if (d.memory) out.push("memory: " + d.memory);
  if (d.confirm) out.push("\n" + d.confirm);
  if (d.next) out.push("\nNEXT: " + d.next);
  return out.filter(Boolean).join("\n");
}

async function forkSubmit(confirmed) {
  report(confirmed ? "forking…" : "checking…");
  lockFork();
  const body = { character: $("b-char").value, as: $("b-name").value.trim(),
                 until: $("b-until").value.trim(),
                 include_sessions: $("b-sessions").checked };
  if (confirmed) body.yes = true;
  let r;
  try {
    r = await api("/admin/roster/fork", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (e) {
    if (e.message === "401") { needToken("that token was refused — try again"); return; }
    report("facade unreachable", true); return;
  }
  const d = r.data || {};
  report(renderForkPlan(d), !d.ok);
  // Fork unlocks only after a clean preview of the SAME inputs (edits re-lock).
  $("b-fork").disabled = !(d.ok && !d.created);
  if (d.created) { lockFork(); refresh(); }
}

$("b-preview").addEventListener("click", () => forkSubmit(false));
$("b-fork").addEventListener("click", () => forkSubmit(true));
$("b-char").addEventListener("change", () => {
  show("b-records", false); $("b-records").textContent = ""; lockFork();
});
for (const id of ["b-name", "b-until", "b-sessions"])
  $(id).addEventListener("input", lockFork);
