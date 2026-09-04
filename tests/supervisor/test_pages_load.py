"""Every served page's JavaScript actually loads.

The splices compose three shared files into six pages at import, and Python can
only prove the strings landed. What it cannot see is the failure this refactor
risks most: a page calling a helper that no longer exists — `say()` after the
status line was unified, `token()` after the shell took it. That is not a syntax
error, so it survives every static check and shows up as a blank page.

So: run each page's script under Node against a stub DOM — the load path, then
every handler the page registered — and fail on ReferenceError alone.

WHAT THIS DOES NOT COVER, measured rather than assumed. A name used only inside
the page's own try/catch (every `api()` call is) is swallowed by that catch and
looks like "facade unreachable"; a name used only while rendering real data
(`el`) is never reached, because the stub answers `{}`. Those two are covered
from the other direction by test_shared_admin_shell: the shell's full text must
appear in every page, so the helpers exist by construction. What is caught here
is the reverse — a page reaching for something no shell provides: `poll`,
`token`, `show`, `report`, `wireToken`, `$`. All six were confirmed by breaking
them on purpose.

This is a smoke test, not a UI test. It asserts that the page LOADS, nothing
about what it renders; the stub is deliberately dumb (every element exists and
does nothing). Skipped when Node is absent — it must never be a build
dependency for a repo that has no build step.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from hearth.control import control as control_mod
from hearth.supervisor import curation as curation_mod
from hearth.supervisor import roster as roster_mod
from hearth.supervisor import routes as routes_mod
from hearth.supervisor import settings as settings_mod

PAGES = {
    "launch": routes_mod._LAUNCH_PAGE,
    "pair": routes_mod._PAIR_PAGE,
    "roster": roster_mod._PAGE,
    "settings": settings_mod._PAGE,
    "memory": curation_mod._PAGE,
    "control": control_mod._HTML,
}

NODE = shutil.which("node")

# A DOM that says yes to everything. Anything a page reaches for exists and does
# nothing, so the only way to fail is to reach for something that was never
# DEFINED — which is exactly the failure being hunted.
HARNESS = r"""
const fs = require("fs");
const handlers = [];
function fakeEl(id) {
  return { id, textContent: "", value: "", className: "", style: {}, options: [],
    dataset: {}, children: [],
    classList: { toggle(){}, add(){}, remove(){}, contains(){ return false; } },
    addEventListener(t, fn){ if (typeof fn === "function") handlers.push(fn); },
    appendChild(c){ return c; }, replaceChildren(){},
    querySelector(){ return fakeEl("q"); }, querySelectorAll(){ return []; },
    insertBefore(){}, remove(){}, focus(){}, setAttribute(){},
    getAttribute(){ return null; }, closest(){ return null; } };
}
global.window = { location: { href: "", pathname: "/", reload(){} },
                  addEventListener(){} };
global.document = { getElementById: (id) => fakeEl(id),
                    createElement: (t) => fakeEl(t),
                    createTextNode: () => fakeEl("text"),
                    querySelector: () => fakeEl("q"), querySelectorAll: () => [],
                    body: fakeEl("body"), addEventListener(){} };
global.localStorage = { getItem: () => "", setItem(){}, removeItem(){} };
global.fetch = async () => ({ status: 200, json: async () => ({}) });
global.setInterval = () => 0;
global.setTimeout = () => 0;
global.navigator = { clipboard: { writeText: async () => {} } };

// A ReferenceError is the whole quarry: it means the page named something that
// does not exist. Anything else (a TypeError off this dumb stub) is the
// harness's own poverty and is ignored.
const misses = [];
function note(e) { if (e instanceof ReferenceError) misses.push(String(e.message)); }
process.on("unhandledRejection", note);

new Function(fs.readFileSync(process.argv[2], "utf8"))();

// Then fire every handler the page registered: report(), show(), the confirm
// flows — the helpers that the load path alone never reaches.
const ev = { key: "Enter", preventDefault(){}, stopPropagation(){},
             target: fakeEl("t"), currentTarget: fakeEl("t") };
for (const fn of handlers) { try { const r = fn(ev); if (r && r.catch) r.catch(note); }
                             catch (e) { note(e); } }
// beforeExit, not setImmediate: an async refresh() rejects a tick later, and
// that rejection is where a missing api() actually surfaces.
process.on("beforeExit", () => {
  if (misses.length) { console.error(misses.join("\n")); process.exit(1); }
});
"""


@unittest.skipUnless(NODE, "node not installed — page smoke test skipped")
class PagesLoad(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._tmp.name)
        cls.harness = cls.dir / "harness.js"
        cls.harness.write_text(HARNESS, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _script(self, name: str, html: str) -> Path:
        js = "\n".join(re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.S))
        path = self.dir / f"{name}.js"
        path.write_text(js, encoding="utf-8")
        return path

    def test_every_page_parses(self):
        for name, page in PAGES.items():
            with self.subTest(page=name):
                path = self._script(name, page())
                r = subprocess.run([NODE, "--check", str(path)],
                                   capture_output=True, text=True, timeout=30)
                self.assertEqual(r.returncode, 0, f"{name}: {r.stderr}")

    def test_every_page_runs_without_naming_something_undefined(self):
        """The load path AND every handler the page registers, with only
        ReferenceError counted as failure — that is precisely 'this page calls
        a helper nobody defines', and nothing about how the stub behaves."""
        for name, page in PAGES.items():
            with self.subTest(page=name):
                path = self._script(name, page())
                r = subprocess.run([NODE, str(self.harness), str(path)],
                                   capture_output=True, text=True, timeout=30)
                self.assertEqual(r.returncode, 0,
                                 f"{name} page failed to load: {r.stderr.strip()}")


if __name__ == "__main__":
    unittest.main()
