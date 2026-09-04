// admin_shell.js — what every authed admin page was re-stating.
//
// Spliced INTO each page's own <script> block, at the placeholder that block
// declares, so these are ordinary top-level declarations in its scope — a page
// that also declares one of them is a SyntaxError, which is the divergence
// guard doing its job at the harshest possible moment. The paired test
// (test_shared_admin_shell.py) catches it before a browser has to.
//
// Four pages take this: launch, roster, settings, memory. pair_page.html
// deliberately does NOT — it is the page a device without the bearer opens, so
// a shell whose whole job is carrying the bearer has nothing to offer it.
//
// The bearer lives in this browser's localStorage and is sent only as an
// Authorization header, never as a query param and never into a cookie jar
// (the carrier cookie is minted server-side from an HMAC — see /admin/cookie).

const $ = (id) => document.getElementById(id);

// Build nodes, so API text never meets innerHTML.
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

const TOKEN_KEY = "hearth_admin_token";  // one key, shared by every admin page

// localStorage throws in private mode and when site data is blocked; a page
// that cannot remember the token must still work, one entry at a time.
function token() {
  try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; }
}

function setToken(value) {
  try { localStorage.setItem(TOKEN_KEY, value); } catch { /* private mode */ }
}

// The authed fetch. `json:` is a convenience: it sets POST + the content type
// and serializes the body, so callers never hand-roll a JSON request.
async function api(path, opts) {
  const o = Object.assign({ headers: {} }, opts || {});
  o.headers["Authorization"] = "Bearer " + token();
  if (o.json !== undefined) {
    o.method = o.method || "POST";
    o.headers["Content-Type"] = "application/json";
    o.body = JSON.stringify(o.json);
    delete o.json;
  }
  const r = await fetch(path, o);
  if (r.status === 401) throw new Error("401");
  let data = null;
  try { data = await r.json(); } catch { /* non-JSON answer */ }
  return { status: r.status, data };
}

function show(id, on) { $(id).classList.toggle("hidden", !on); }

// The page's one status line: #report where a page has one, #msg otherwise.
function report(text, isErr) {
  const out = $("report") || $("msg");
  if (!out) return;
  out.textContent = text || "";
  out.className = isErr ? "err" : "";
}

// Token entry: save, clear the field (it must not linger in the DOM), reload.
// Enter in the field is the same gesture as the button.
function wireToken(refresh) {
  $("tokenbtn").addEventListener("click", () => {
    setToken($("token").value.trim());
    $("token").value = "";
    refresh();
  });
  $("token").addEventListener("keydown", (e) => {
    if (e.key === "Enter") $("tokenbtn").click();
  });
}

// Render now, then on a cadence. Omit `ms` for a page that only renders once
// (a form page has nothing that changes underneath it).
function poll(refresh, ms) {
  refresh();
  if (ms) setInterval(refresh, ms);
}
