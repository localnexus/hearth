// settings_schema.js — walking pydantic v2 JSON Schema.
// Spliced INSIDE settings_page.html's own <script> block
// (ui/settings_sections.py), so the admin shell's helpers and the page's
// own state are already in scope. Pure helpers: reads nothing, used by settings_form.js.
//
// ── schema walking (pydantic v2 JSON Schema) ─────────────────────────────────

function derefIn(defs, s) {
  let g = s, hops = 0;
  while (g && g.$ref && hops++ < 10) g = defs[g.$ref.split("/").pop()] || null;
  return g || {};
}

function unwrap(defs, s) {
  // Keep the outer wrapper's metadata (description, default, x-hearth) while
  // resolving Optional (anyOf with null), allOf-wrapped and $ref'd cores.
  const meta = {};
  for (const k of ["description", "default", "x-hearth"])
    if (s[k] !== undefined) meta[k] = s[k];
  let core = s;
  if (core.anyOf) {
    const nn = core.anyOf.filter((x) => x.type !== "null");
    core = nn.length === 1 ? nn[0] : {};
  }
  if (core.allOf && core.allOf.length === 1) core = core.allOf[0];
  core = derefIn(defs, core);
  return Object.assign({}, core, meta);
}
