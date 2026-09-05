"""test_manual_lint.py — the plain words stay on every surface a stranger reads.

Headless: fixtures are strings and temp files. Pins:

  1. prose in Markdown is flagged; fenced code, inline code, indented code,
     link targets and a word glued to path punctuation are not;
  2. the allow mark on a line suppresses it;
  3. JS: only string literals that read as prose (contain a space) count;
     comments and bare names ("Bearer ") do not;
  4. HTML: text between tags counts; comments and inline scripts are handled
     (script bodies as JS, comments exempt);
  5. Python: docstrings and logger.* arguments are exempt; a reply string is not;
  6. the corpus walk finds the four kinds and nothing else; --strict flips exit.

Run:  .venv/bin/python -m unittest tests.test_manual_lint
"""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from hearth.tools import manual_lint as ml

P = Path("zz.md")


def words(findings):
    return sorted(f.word.lower() for f in findings)


class Markdown(unittest.TestCase):
    def test_prose_is_flagged_and_identifier_positions_are_not(self):
        text = "\n".join([
            "Start the bot, then open the facade.",      # 2 findings
            "Run `python -m hearth.pipeline.bot` now.",  # inline code: none
            "See hearth.pipeline.bot and serve.supervisor.enabled.",  # glued: none
            "Press [the launch page](docs/bot/launch.md).",  # link target: none
            "```",
            "the bot is a facade",  # fenced: none
            "```",
            "    a bot in indented code",  # indented: none
            "The /admin/bot/start route and --memory flag.",  # glued: none
        ])
        self.assertEqual(words(ml.scan_markdown(text, P)), ["bot", "facade"])

    def test_fence_inside_a_blockquote_is_code(self):
        text = "> the facade\n> ```bash\n> curl -H \"Authorization: Bearer x\" u\n> ```\n> bearer again"
        self.assertEqual([f.word for f in ml.scan_markdown(text, P)], ["facade", "bearer"])

    def test_allow_mark_suppresses_the_line(self):
        self.assertEqual(ml.scan_markdown("the bot  <!-- manual-lint: allow -->", P), [])

    def test_allow_file_mark_skips_the_file(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "zz.md"
            f.write_text("<!-- manual-lint: allow-file -->\nthe bot and the facade\n")
            self.assertEqual(ml.scan_file(f), [])

    def test_case_and_multiword_terms(self):
        f = ml.scan_markdown("Your Data Root holds the LLM's files; a Bearer is minted.", P)
        self.assertEqual(words(f), ["bearer", "data root", "llm"])
        self.assertTrue(all(str(x).startswith("zz.md:1:") for x in f))


class JsAndHtml(unittest.TestCase):
    def test_js_prose_literals_only(self):
        js = "\n".join([
            "// the bot is a comment",
            "const h = 'Bearer ' + key;",           # bare name with a trailing space
            "const m = `the bot is down`;",         # prose → 1
            'const k = "bot";',                    # bare name
            "/* the facade, in a block comment */",
        ])
        f = ml.scan_js(js, Path("zz.js"))
        self.assertEqual([(x.word, x.line) for x in f], [("bot", 3)])

    def test_html_text_scripts_and_comments(self):
        html = "\n".join([
            "<!-- the facade lives here -->",
            "<p>enter the bearer token</p>",
            "<script>const s = 'facade unreachable — retrying';</script>",
            "<pre>the bot in a pre block</pre>",
        ])
        f = ml.scan_html(html, Path("zz.html"))
        self.assertEqual(sorted((x.word, x.line) for x in f), [("bearer", 2), ("facade", 3)])


class Python(unittest.TestCase):
    def test_docstrings_and_logs_exempt_replies_not(self):
        src = '\n'.join([
            '"""the facade daemon, in a module docstring."""',
            'from loguru import logger',
            'def f():',
            '    """the supervisor spawns the bot."""',
            '    logger.info("bot started (pid {})", 1)',
            '    return {"error": "the bot is down", "mode": "bot"}',
        ])
        f = ml.scan_python(src, Path("zz.py"))
        self.assertEqual([(x.word, x.line) for x in f], [("bot", 6)])

    def test_fstring_parts_count_and_allow_mark_works(self):
        src = 'x = f"the bot answered {code}"\ny = "the daemon is up"  # manual-lint: allow\n'
        f = ml.scan_python(src, Path("zz.py"))
        self.assertEqual([(x.word, x.line) for x in f], [("bot", 1)])


class Walk(unittest.TestCase):
    def test_corpus_kinds_and_strict_exit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for rel, body in {
                "docs/a.md": "the bot\n",
                "README.md": "the facade\n",
                "src/hearth/x/p.html": "<p>the daemon</p>\n",
                "src/hearth/ui/u.js": "const a = 'the bearer is here';\n",
                "src/hearth/serve/s.py": 'm = "the LLM is down"\n',
                "src/hearth/pipeline/ignored.py": 'm = "the bot is out of scope here"\n',
                "src/hearth/ui/ignored.py": 'm = "the bot is out of scope here"\n',
            }.items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text(body)
            files = {str(p.relative_to(root)) for p in ml.corpus(root)}
            self.assertEqual(files, {"docs/a.md", "README.md", "src/hearth/x/p.html",
                                     "src/hearth/ui/u.js", "src/hearth/serve/s.py"})
            out = io.StringIO()
            with redirect_stdout(out):
                warn = ml.main(["--root", td])
                strict = ml.main(["--root", td, "--strict"])
                summ = ml.main(["--root", td, "--summary"])
            self.assertEqual((warn, strict, summ), (0, 1, 0))
            text = out.getvalue()
            self.assertIn("WARN — 5 finding(s) in 5 file(s), 5 scanned", text)
            self.assertIn("DIRTY — 5 finding(s)", text)
            self.assertIn("    1  docs/a.md", text)
            self.assertNotIn("ignored", text)

    def test_words_lists_every_term(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(ml.main(["--words"]), 0)
        for w in ml.WORDS:
            self.assertIn(w, out.getvalue())


if __name__ == "__main__":
    unittest.main()
