"""admin_shell.js's poll() — a hidden tab stops polling, and a tick never
overlaps a still-running refresh.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from hearth.ui import admin_shell

NODE = shutil.which("node")

POLL_RE = re.compile(r"function poll\(.*?\n\}", re.S)


class AdminShellPollStatic(unittest.TestCase):
    """Assertions that need no interpreter — always run."""

    def test_hidden_and_visibilitychange_are_wired(self):
        self.assertIn("document.hidden", admin_shell.JS)
        self.assertIn("visibilitychange", admin_shell.JS)

    def test_poll_is_extractable(self):
        self.assertIsNotNone(POLL_RE.search(admin_shell.JS), "poll() not found in admin_shell.js")


HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");

function expect(cond, msg) {
  if (!cond) { console.error(msg); process.exit(1); }
}

function makeDoc(hidden) {
  return { hidden, listeners: {}, addEventListener(t, f) { this.listeners[t] = f; } };
}

function run(src, refresh, ms) {
  const intervalCalls = [];
  global.setInterval = (fn, interval) => { intervalCalls.push({ fn, ms: interval }); return 1; };
  return { intervalCalls, call: () => new Function("refresh", "ms", src + "\nreturn poll(refresh, ms);")(refresh, ms) };
}

async function main() {
  // (a) poll(refresh) -> renders once, registers no interval.
  {
    global.document = makeDoc(false);
    let calls = 0;
    const refresh = () => { calls += 1; return Promise.resolve(); };
    const { intervalCalls, call } = run(src, refresh, undefined);
    call();
    expect(calls === 1, "a: expected refresh called once, got " + calls);
    expect(intervalCalls.length === 0, "a: expected no interval, got " + intervalCalls.length);
  }

  // (b) poll(refresh, 500) -> interval registered at ms === 2000 (floor).
  {
    global.document = makeDoc(false);
    const refresh = () => Promise.resolve();
    const { intervalCalls, call } = run(src, refresh, 500);
    call();
    expect(intervalCalls.length === 1, "b: expected one interval, got " + intervalCalls.length);
    expect(intervalCalls[0].ms === 2000, "b: expected ms 2000, got " + intervalCalls[0].ms);
  }

  // (c) a tick while hidden does not refresh; the same tick while visible does.
  {
    const doc = makeDoc(true);
    global.document = doc;
    let calls = 0;
    const refresh = () => { calls += 1; return Promise.resolve(); };
    const { intervalCalls, call } = run(src, refresh, 500);
    call();
    calls = 0; // isolate from the unconditional initial render
    const tick = intervalCalls[0].fn;
    await tick();
    expect(calls === 0, "c: hidden tick called refresh");
    doc.hidden = false;
    await tick();
    expect(calls === 1, "c: visible tick did not call refresh");
  }

  // (d) a still-pending refresh blocks a second tick from starting another.
  {
    global.document = makeDoc(false);
    let calls = 0;
    let resolvePending;
    const pending = new Promise((res) => { resolvePending = res; });
    const refresh = () => { calls += 1; return pending; };
    const { intervalCalls, call } = run(src, refresh, 500);
    call();
    calls = 0; // isolate from the unconditional initial render
    const tick = intervalCalls[0].fn;
    const first = tick();
    expect(calls === 1, "d: first tick did not start a refresh");
    tick(); // must return at once: a refresh is already in flight
    expect(calls === 1, "d: second tick started an overlapping refresh");
    resolvePending();
    await first;
  }

  // (e) a visibilitychange while visible triggers an immediate refresh.
  {
    const doc = makeDoc(false);
    global.document = doc;
    let calls = 0;
    const refresh = () => { calls += 1; return Promise.resolve(); };
    const { call } = run(src, refresh, 500);
    call();
    calls = 0; // isolate from the unconditional initial render
    doc.listeners["visibilitychange"]();
    await Promise.resolve();
    expect(calls === 1, "e: visibilitychange did not call refresh");
  }

  console.log("OK");
  process.exit(0);
}

main();
"""


@unittest.skipUnless(NODE, "node not installed — admin_shell poll node tests skipped")
class AdminShellPollNode(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_poll_hidden_backoff_and_overlap_guard(self):
        match = POLL_RE.search(admin_shell.JS)
        self.assertIsNotNone(match, "poll() not found in admin_shell.js")
        poll_src = self.dir / "poll_src.js"
        poll_src.write_text(match.group(0), encoding="utf-8")
        harness = self.dir / "poll_harness.js"
        harness.write_text(HARNESS, encoding="utf-8")
        r = subprocess.run([NODE, str(harness), str(poll_src)],
                            capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr.strip() or r.stdout.strip())
        self.assertIn("OK", r.stdout)
