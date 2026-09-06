"""test_banner.py — the first-run banner stays decorative and stays in step.

  1. no terminal → nothing printed (init's output stays script-readable)
  2. a terminal → the seven lines, coloured; NO_COLOR → the same lines, bare
  3. bounds: at most 8 lines, none wider than 60 columns; ends with a blank
  4. quiet: --quiet or HEARTH_BANNER_SHOWN=1 suppress it even on a terminal
  5. parity: install.sh (when it exists) carries a byte-identical copy of ART
  6. end to end: python -m hearth.init --yes on a pipe never prints the art

Run:  .venv/bin/python -m unittest tests/test_banner.py
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hearth.config import config_loader as cl
from hearth.init import banner

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


class _Stream(io.StringIO):
    def __init__(self, tty: bool):
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


class BannerTests(unittest.TestCase):
    def test_no_terminal_prints_nothing(self):
        s = _Stream(tty=False)
        self.assertFalse(banner.show(s, env={}))
        self.assertEqual(s.getvalue(), "")

    def test_terminal_prints_the_art_coloured_or_bare(self):
        s = _Stream(tty=True)
        self.assertTrue(banner.show(s, env={}))
        out = s.getvalue()
        self.assertIn("\x1b[", out)
        self.assertEqual(_ANSI.sub("", out), "\n".join(banner.ART) + "\n\n")
        bare = _Stream(tty=True)
        banner.show(bare, env={"NO_COLOR": "1"})
        self.assertNotIn("\x1b[", bare.getvalue())
        self.assertEqual(bare.getvalue(), "\n".join(banner.ART) + "\n\n")

    def test_bounds(self):
        self.assertLessEqual(len(banner.ART), 8)
        for line in banner.ART:
            self.assertLessEqual(len(line), 60, line)
            self.assertEqual(line, line.rstrip(), "no trailing spaces")
        self.assertIn("H E A R T H", "\n".join(banner.ART))

    def test_quiet_and_already_shown(self):
        s = _Stream(tty=True)
        self.assertFalse(banner.show(s, env={}, quiet=True))
        self.assertFalse(banner.show(s, env={banner.SHOWN_ENV: "1"}))
        self.assertEqual(s.getvalue(), "")

    def test_install_script_carries_the_same_art(self):
        script = cl._ROOT / "install.sh"
        if not script.is_file():
            self.skipTest("install.sh not shipped yet (stroke B)")
        body = script.read_text(encoding="utf-8")
        self.assertIn("\n".join(banner.ART) + "\n", body,
                      "install.sh's banner heredoc drifted from hearth.init.banner.ART")

    def test_init_on_a_pipe_never_prints_the_art(self):
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ)
            env.pop("HEARTH_DATA", None)
            env.update(HEARTH_DATA=td, HEARTH_ROOT=str(cl._ROOT), PYTHONDONTWRITEBYTECODE="1")
            r = subprocess.run([sys.executable, "-m", "hearth.init", "--yes", "--no-probe"],
                               capture_output=True, text=True, env=env, cwd=str(cl._ROOT),
                               stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr[-600:])
        self.assertNotIn("H E A R T H", r.stdout)
        self.assertTrue(r.stdout.startswith("Hearth first run"), r.stdout[:80])


if __name__ == "__main__":
    unittest.main()
