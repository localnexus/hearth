"""test_install_usage.py — --help works under `curl | bash`, and says the true thing.

  1. the embedded USAGE matches the comment block at the top of install.sh
  2. --help prints it with no script file to read from (the curl | bash case)
  3. every flag the parser accepts is named in it

No import of the hearth package: this tests the shell script alone, so it runs
before the venv exists — which is the situation install.sh is written for.

Run:  .venv/bin/python -m unittest tests/test_install_usage.py
      python3 -m unittest tests.test_install_usage
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "install.sh"


def _header_block(body: str) -> str:
    """Every contiguous comment line after the shebang, de-hashed. No line numbers:
    the block grows when a flag is added, and the test must not need editing for that."""
    out = []
    for line in body.splitlines()[1:]:
        if not line.startswith("#"):
            break
        out.append(re.sub(r"^# ?", "", line))
    return "\n".join(out)


def _embedded(body: str) -> str:
    m = re.search(r"<<'USG'[^\n]*\n(.*?)\nUSG\n", body, re.S)
    assert m, "the USAGE heredoc is gone from install.sh"
    return m.group(1)


class InstallUsageTests(unittest.TestCase):
    def setUp(self):
        if not SCRIPT.is_file():
            self.skipTest("install.sh not shipped yet")
        self.body = SCRIPT.read_text(encoding="utf-8")

    def test_embedded_usage_matches_the_header_block(self):
        self.assertEqual(_embedded(self.body), _header_block(self.body),
                         "install.sh's USAGE heredoc drifted from its own header comment")

    def test_help_works_with_no_script_file(self):
        """`curl … | bash` hands bash the script on stdin; $0 is the bare word 'bash',
        so anything that reads $0 is reading a file that is not there."""
        r = subprocess.run(["bash", "-s", "--", "--help"], input=self.body,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertEqual(r.stdout.rstrip("\n"), _embedded(self.body))
        self.assertNotIn("\x00", r.stdout, "that is a binary, not the usage text")

    def test_every_parsed_flag_is_documented(self):
        parsed = set(re.findall(r"^\s*(--[a-z-]+)(?:\|-\w)?\)", self.body, re.M))
        self.assertGreater(len(parsed), 5, "the argument parser stopped being readable")
        out = _embedded(self.body)
        for flag in sorted(parsed - {"--help"}):
            self.assertIn(flag, out, f"{flag} is accepted but not in --help")


if __name__ == "__main__":
    unittest.main()
