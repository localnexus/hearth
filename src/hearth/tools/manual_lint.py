"""manual_lint — keep the plain words on every surface a stranger reads.

The plain-words pass (2026-09-05) replaced Hearth's internal names in prose:
facade → Hearth, bearer → access key, bot → companion, LLM server → model
server. It was done by hand, once. This check is the standing version: it
walks the reader-facing corpus and reports each internal name that appears in
PROSE, with the plain word to use instead.

The corpus is everything a stranger reads, not only Markdown:
  - Markdown: docs/, the in-app users-manual, README.md
  - page HTML under src/hearth (text between tags; scripts as JS; comments exempt)
  - JS string literals in src/hearth/ui/*.js
  - Python string literals in init/, supervisor/ and serve/ — the JSON replies,
    prints and page text a page shows verbatim. Docstrings and logger.* calls
    are exempt: only the operator reads those.

Identifier position is the carve-out: fenced code, inline `code`, and a word
glued to path/attribute punctuation (hearth.pipeline.bot, serve.supervisor,
/admin/bot/start, --memory) is a name, not prose, and passes. A string
literal without a space is a name too (a mode, a key) and is skipped whole.
A line carrying `manual-lint: allow` is never flagged; a file carrying
`manual-lint: allow-file` is skipped whole (a page whose subject is the words).

Warning-level by default (exit 0). `--strict` exits 1 on findings so the
check can become a gate once its false-positive rate has been read.

Run:  python -m hearth.tools.manual_lint [PATHS…] [--strict] [--words]
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

# internal name → the plain word (also the reason shown per finding)
WORDS: dict[str, str] = {
    "facade": "Hearth (the running program)",
    "bearer": "access key",
    "bot": "companion",
    "daemon": "Hearth (the running program)",
    "supervisor": "Hearth / the launch page",
    "gate": "switch, or setting",
    "bootstrap": "first-run setup",
    "data root": "data folder",
    "0600": "readable only by you",
    "kernel": "the machine, or the system",
    "LLM": "model, or model server",
}
ALLOW_MARK = "manual-lint: allow"
ALLOW_FILE_MARK = "manual-lint: allow-file"  # anywhere in a file: the words are its subject

_MD_DIRS = ("docs", "src/hearth/config/users-manual")
_MD_FILES = ("README.md",)
_PY_DIRS = ("src/hearth/init", "src/hearth/supervisor", "src/hearth/serve")
_JS_DIRS = ("src/hearth/ui",)
_HTML_ROOT = "src/hearth"

# a word glued (no space) to punctuation that only names carry
_IDENT_GLUE = set("._/-:={}@#$%\\")
_WORD_RE = re.compile(
    r"(?<![\w-])(" + "|".join(re.escape(w) for w in sorted(WORDS, key=len, reverse=True))
    + r")(?![\w-])", re.IGNORECASE)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_LINK_TARGET = re.compile(r"\]\([^)\n]*\)")
_HTML_TAG = re.compile(r"<[^>]+>")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_HTML_SCRIPT = re.compile(r"<(script|style)\b[^>]*>(.*?)</\1>", re.S | re.I)
_JS_TOKEN = re.compile(
    r"//[^\n]*|/\*.*?\*/|\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`", re.S)


class Finding:
    __slots__ = ("path", "line", "word", "context")

    def __init__(self, path: Path, line: int, word: str, context: str) -> None:
        self.path, self.line, self.word, self.context = path, line, word, context

    def __str__(self) -> str:
        why = WORDS[_canon(self.word)]
        return f"{self.path}:{self.line}: [{_canon(self.word)}] '{self.context}' — plain word: {why}"


def _canon(word: str) -> str:
    low = word.lower()
    for w in WORDS:
        if w.lower() == low:
            return w
    return word


def _in_identifier_position(text: str, start: int, end: int) -> bool:
    # glue only when something non-blank sits beyond the punctuation: "a.bot"
    # and "bot/start" are names; "the bot." and "bot: press" are sentences.
    before = text[start - 1] if start > 0 else " "
    beyond_b = text[start - 2] if start > 1 else " "
    after = text[end] if end < len(text) else " "
    beyond_a = text[end + 1] if end + 1 < len(text) else " "
    return ((before in _IDENT_GLUE and not beyond_b.isspace())
            or (after in _IDENT_GLUE and not beyond_a.isspace()))


def scan_prose(text: str, path: Path, line_no: int) -> list[Finding]:
    """Findings in one prose line (already stripped of code spans)."""
    out: list[Finding] = []
    if ALLOW_MARK in text:
        return out
    for m in _WORD_RE.finditer(text):
        if _in_identifier_position(text, m.start(), m.end()):
            continue
        lo, hi = max(0, m.start() - 24), min(len(text), m.end() + 24)
        out.append(Finding(path, line_no, m.group(1), text[lo:hi].strip()))
    return out


# ── Markdown ──────────────────────────────────────────────────────────────────

def scan_markdown(text: str, path: Path) -> list[Finding]:
    out: list[Finding] = []
    fenced = False
    for i, raw in enumerate(text.splitlines(), 1):
        stripped = raw.lstrip()
        if stripped.startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced or raw.startswith("    "):
            continue  # fenced or indented code
        line = _INLINE_CODE.sub(" ", raw)
        line = _LINK_TARGET.sub("]", line)
        out += scan_prose(line, path, i)
    return out


# ── JS / HTML ─────────────────────────────────────────────────────────────────

def scan_js(text: str, path: Path, line_base: int = 0) -> list[Finding]:
    """String literals that read as prose (contain a space); comments skipped."""
    out: list[Finding] = []
    for m in _JS_TOKEN.finditer(text):
        tok = m.group(0)
        if tok[0] in "/":
            continue
        body = tok[1:-1]
        if " " not in body.strip():  # a bare name ("Bearer ", "bot") is not prose
            continue
        line = line_base + text.count("\n", 0, m.start()) + 1
        for j, part in enumerate(body.split("\n")):
            out += scan_prose(part, path, line + j)
    return out


def scan_html(text: str, path: Path) -> list[Finding]:
    out: list[Finding] = []
    for m in _HTML_SCRIPT.finditer(text):
        base = text.count("\n", 0, m.start(2))
        if m.group(1).lower() == "script":
            out += scan_js(m.group(2), path, base)
    blank = lambda m: "\n" * m.group(0).count("\n")  # noqa: E731 — keep line numbers
    stripped = _HTML_COMMENT.sub(blank, _HTML_SCRIPT.sub(blank, text))
    for i, raw in enumerate(stripped.splitlines(), 1):
        if "<code" in raw or "<pre" in raw:
            continue
        out += scan_prose(_HTML_TAG.sub(" ", raw), path, i)
    return out


# ── Python ────────────────────────────────────────────────────────────────────

def _is_log_call(node: ast.AST) -> bool:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        base = node.func.value
        name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")
        return name in ("logger", "log", "logging", "_log", "_logger")
    return False


def scan_python(text: str, path: Path) -> list[Finding]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    skip: set[int] = set()  # node ids of docstrings and logger args
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                skip.add(id(body[0].value))
        if _is_log_call(node):
            for sub in ast.walk(node):
                skip.add(id(sub))
    out: list[Finding] = []
    lines = text.splitlines()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in skip or " " not in node.value.strip():
            continue
        line = node.lineno
        src_line = lines[line - 1] if line - 1 < len(lines) else ""
        if ALLOW_MARK in src_line:
            continue
        for j, part in enumerate(node.value.split("\n")):
            out += scan_prose(part, path, line + j)
    return out


# ── the walk ──────────────────────────────────────────────────────────────────

def corpus(root: Path) -> list[Path]:
    files: list[Path] = []
    for d in _MD_DIRS:
        files += sorted((root / d).rglob("*.md"))
    files += [root / f for f in _MD_FILES if (root / f).is_file()]
    files += sorted((root / _HTML_ROOT).rglob("*.html"))
    for d in _JS_DIRS:
        files += sorted((root / d).glob("*.js"))
    for d in _PY_DIRS:
        files += sorted((root / d).rglob("*.py"))
    return [f for f in files if f.is_file()]


def scan_file(path: Path, root: Path | None = None) -> list[Finding]:
    text = path.read_text(encoding="utf-8", errors="replace")
    if ALLOW_FILE_MARK in text:
        return []
    shown = path.relative_to(root) if root and path.is_relative_to(root) else path
    if path.suffix == ".md":
        return scan_markdown(text, shown)
    if path.suffix == ".html":
        return scan_html(text, shown)
    if path.suffix == ".js":
        return scan_js(text, shown)
    if path.suffix == ".py":
        return scan_python(text, shown)
    return []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="manual-lint", description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="*", help="files or dirs (default: the reader-facing corpus)")
    ap.add_argument("--root", default=".", help="repo root (default: cwd)")
    ap.add_argument("--strict", action="store_true", help="exit 1 on findings (default: warn)")
    ap.add_argument("--words", action="store_true", help="print the word list and exit")
    ap.add_argument("--summary", action="store_true", help="per-file counts only")
    a = ap.parse_args(argv)
    if a.words:
        for w, plain in WORDS.items():
            print(f"{w:10s} → {plain}")
        return 0
    root = Path(a.root).resolve()
    files: list[Path] = []
    for p in a.paths:
        pp = Path(p)
        files += sorted(x for x in pp.rglob("*") if x.is_file()) if pp.is_dir() else [pp]
    if not a.paths:
        files = corpus(root)
    findings: list[Finding] = []
    for f in files:
        findings += scan_file(f, root)
    if a.summary:
        counts: dict[str, int] = {}
        for f in findings:
            counts[str(f.path)] = counts.get(str(f.path), 0) + 1
        for path, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"{n:5d}  {path}")
    else:
        for f in findings:
            print(f)
    n_files = len({f.path for f in findings})
    verdict = "CLEAN" if not findings else ("DIRTY" if a.strict else "WARN")
    print(f"manual-lint: {verdict} — {len(findings)} finding(s) in {n_files} file(s), {len(files)} scanned")
    return 1 if (findings and a.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
