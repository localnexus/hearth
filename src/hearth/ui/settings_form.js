// settings_form.js — the generated form.
// Spliced INSIDE settings_page.html's own <script> block
// (ui/settings_sections.py), so the admin shell's helpers and the page's
// own state are already in scope. Reads unwrap() (schema), renderKinds() (files), askSet() (confirm).
//
// ── the generated form ───────────────────────────────────────────────────────

async function openFile(label) {
  clearPending(); report("");
  let r;
  try { r = await api("/admin/settings/file?file=" + encodeURIComponent(label)); }
  catch (e) {
    if (e.message === "401") { needToken("that token was refused — try again"); return; }
    report("facade unreachable", true); return;
  }
  if (r.status !== 200) { report((r.data || {}).error || "load failed", true); return; }
  current = r.data;
  renderKinds();  // repaint selection highlight
  renderForm();
}

function effectBadge(xh, kindInfo) {
  const b = document.createElement("span"); b.className = "badge";
  if (xh.hot_via) { b.classList.add("live"); b.textContent = "live · " + xh.hot_via; }
  else {
    const when = xh.effect || kindInfo.restart;
    b.textContent = when === "none" ? "live layer" : "at " + when + " restart";
    if (xh.effect_note) b.title = xh.effect_note;
  }
  return b;
}

function effectText(eff) {
  if (eff.hot_via)
    return "live path: " + eff.hot_via + " — the running lane honors this at the next turn boundary";
  const when = eff.effect || eff.restart;
  if (when === "none") return "live layer — polled at the next turn boundary";
  let t = "this edit lands at the next " + when + " restart — nothing changes now";
  if (eff.effect_note) t += "\n(" + eff.effect_note + ")";
  return t;
}

function kindInfoFor(kind) {
  for (const k of (overview || {}).kinds || []) if (k.kind === kind) return k;
  return { restart: "?" };
}

function renderForm() {
  const ks = schemas[current.kind] || {};
  const defs = (ks.schema && ks.schema.$defs) || {};
  const kindInfo = kindInfoFor(current.kind);
  $("formhead").textContent = (ks.title || current.kind) + " — " + current.file;
  const note = $("formnote"); note.textContent = "";
  if (!current.writable && current.pointer) {
    const p = document.createElement("p"); p.className = "err";
    p.textContent = "read-only here: " + current.pointer;
    note.appendChild(p);
  }
  if (ks.note) {
    const p = document.createElement("p"); p.appendChild(document.createElement("small"))
      .textContent = ks.note;
    note.appendChild(p);
  }
  const v = $("verdict");
  const lines = ["verdict: " + current.verdict];
  for (const e of current.errors || []) lines.push("  - " + e);
  for (const w of current.warnings || []) lines.push("  ~ " + w);
  v.textContent = lines.join("\n");
  v.className = (current.errors || []).length ? "err" : "";
  const box = $("fields");
  box.textContent = "";
  renderFields(box, defs, ks.schema || {}, "", current.values || {}, kindInfo);
  show("formcard", true);
}

function renderFields(box, defs, modelSchema, prefix, values, kindInfo) {
  const props = modelSchema.properties || {};
  for (const [name, raw] of Object.entries(props)) {
    const s = unwrap(defs, raw);
    const key = prefix ? prefix + "." + name : name;
    const xh = s["x-hearth"] || {};
    const val = values ? values[name] : undefined;
    if (s.properties) {  // sub-table: header + recurse
      const h = document.createElement("div"); h.className = "subhead";
      h.textContent = "[" + key + "]" + (val === undefined ? "  (unset)" : "");
      box.appendChild(h);
      renderFields(box, defs, s, key,
                   (val && typeof val === "object") ? val : null, kindInfo);
      continue;
    }
    if (s.additionalProperties) {  // map field
      const inner = unwrap(defs, s.additionalProperties);
      if (inner.properties || xh.secret) {  // structured or secret map: read-only
        addRow(box, key, s, xh, val, kindInfo, { structured: !xh.secret });
        continue;
      }
      addMapRows(box, key, s, inner, xh, val || {}, kindInfo);
      continue;
    }
    const structured = s.type === "array" || s.type === "object";
    addRow(box, key, s, xh, val, kindInfo, { structured });
  }
  // Keys present in the file but not declared — the verdict flags them; show them.
  for (const name of Object.keys(values || {})) {
    if (!(name in props)) {
      const key = prefix ? prefix + "." + name : name;
      addRow(box, key, { description: "(not in the declared schema)" }, {},
             values[name], kindInfo, { structured: true });
    }
  }
}

function fmtVal(v) {
  if (v === undefined) return "(unset)";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function addRow(box, key, s, xh, val, kindInfo, opts) {
  const row = document.createElement("div"); row.className = "field";
  const top = document.createElement("div"); top.className = "top";
  const k = document.createElement("span"); k.className = "key"; k.textContent = key;
  top.appendChild(k);

  const editable = current.writable && !xh.secret && !opts.structured;
  let control = null, save = null;
  if (xh.secret) {
    const ro = document.createElement("span"); ro.className = "ro";
    ro.textContent = fmtVal(val) + "  (secret — desk only)";
    top.appendChild(ro);
  } else if (!editable) {
    const ro = document.createElement("span"); ro.className = "ro";
    ro.textContent = fmtVal(val) + (opts.structured && current.writable
                                    ? "  (structured — edit the file by hand)" : "");
    top.appendChild(ro);
  } else {
    control = makeControl(s, val);
    save = document.createElement("button");
    save.textContent = "Save…"; save.disabled = true;
    const initial = control.value;
    control.addEventListener("input", () => { save.disabled = control.value === initial; });
    save.addEventListener("click", () => askSet(key, s, control.value));
    top.append(control, save);
  }
  const badge = effectBadge(xh, kindInfo);
  top.appendChild(badge);
  if (s.default !== undefined && s.default !== null) {
    const d = document.createElement("small");
    d.textContent = "default: " + fmtVal(s.default);
    top.appendChild(d);
  }
  row.appendChild(top);
  if (s.description) {
    const p = document.createElement("p"); p.className = "desc";
    p.textContent = s.description;
    row.appendChild(p);
  }
  box.appendChild(row);
}

function makeControl(s, val) {
  if (s.type === "boolean" || s.enum) {
    const sel = document.createElement("select");
    const opts = s.enum ? s.enum.map(String) : ["true", "false"];
    if (val === undefined) {
      const o = document.createElement("option");
      o.value = ""; o.textContent = "(unset)";
      sel.appendChild(o);
    }
    for (const v of opts) {
      const o = document.createElement("option");
      o.value = v; o.textContent = v;
      sel.appendChild(o);
    }
    sel.value = val === undefined ? "" : String(val);
    return sel;
  }
  const inp = document.createElement("input");
  if (s.type === "integer" || s.type === "number") {
    inp.type = "number";
    if (s.minimum !== undefined) inp.min = s.minimum;
    if (s.maximum !== undefined) inp.max = s.maximum;
    if (s.exclusiveMinimum !== undefined) inp.min = s.exclusiveMinimum;
    inp.step = s.type === "integer" ? "1" : "any";
  } else {
    inp.type = "text";
  }
  inp.value = val === undefined ? "" : String(val);
  if (val === undefined) inp.placeholder = "(unset)";
  return inp;
}

function addMapRows(box, mapKey, s, inner, xh, entries, kindInfo) {
  const h = document.createElement("div"); h.className = "subhead";
  h.textContent = "[" + mapKey + "]";
  box.appendChild(h);
  if (s.description) {
    const p = document.createElement("p"); p.className = "desc";
    p.textContent = s.description;
    box.appendChild(p);
  }
  for (const [name, val] of Object.entries(entries))
    addRow(box, mapKey + "." + name, inner, xh, val, kindInfo, {});
  if (!current.writable) return;
  const row = document.createElement("div"); row.className = "field";
  const top = document.createElement("div"); top.className = "top";
  const nameIn = document.createElement("input");
  nameIn.type = "text"; nameIn.placeholder = "new entry name";
  const valIn = makeControl(inner, undefined);
  const add = document.createElement("button"); add.textContent = "Add…";
  add.addEventListener("click", () => {
    const n = nameIn.value.trim();
    if (!n) { report("entry name required", true); return; }
    askSet(mapKey + "." + n, inner, valIn.value);
  });
  top.append(nameIn, valIn, add);
  row.appendChild(top);
  box.appendChild(row);
}
