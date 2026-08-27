"""features/manual.py — the users-manual contributor.

DROP-IN, ACTIVE via bot.py's feature-import block (`import features.manual`). Remove
that line and the routes vanish → the page's /manual/index probe fails → the left
rail never shows (standard degradation). `control.py` needs NO edits (it is the stable
route seam).

WHAT IT DOES
    GET /manual/index        → {ok, pages: [{name, title}]}   (README.md pinned first)
    GET /manual/page/{name}  → {ok, name, title, html}        (rendered fragment)
    Read-only, loopback-bound like the rest of the panel. The content pane fetches
    fragments into the live page — nothing navigates, so panel state (status poll,
    draft text, mute/PTT, an in-flight recording) survives by construction.

SCOPE FENCE (in code, not convention)
    Serves ONLY users-manual/*.md: {name} must match _NAME_RE, end in .md, and resolve
    directly inside _MANUAL_DIR — anything else is a named 400/404. The manual cannot
    quietly become a docs server (the ADR's fence).

RENDERER (Decision 1: the tiny in-repo option — zero deps, ~150 lines)
    A subset renderer covering exactly what the nine pages use (proposal survey):
    h1–h4 · bold/italic/inline code · fenced code · tables · blockquotes · rules ·
    flat bullet/numbered lists (wrap-continuations absorbed) · links. Everything is
    HTML-escaped before any markup is emitted — there is NO raw-HTML passthrough, so
    a `<script>` in a page can never go live. Render-on-request with an mtime cache
    (edits land live; no build step to go stale). Fail-soft: a page that breaks the
    renderer degrades to escaped raw text in a <pre>.

LINK POLICY
    same-dir *.md  → <a data-page="…">   (the pane intercepts; no navigation)
    http(s)://…    → normal anchor, new tab
    anything else  → inert <span class="deadlink" title="lives in the repo: …">
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from aiohttp import web
from loguru import logger

from hearth.config import config_loader
from hearth.control.control_routes import PanelContext, register

# The users-manual pages ship inside this package (src/hearth/config/users-manual/),
# not under the repo-root asset tree, so anchor on the config package directory.
_MANUAL_DIR: Path = Path(config_loader.__file__).resolve().parent / "users-manual"
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# name → (mtime, title, html, degraded): render once per file revision.
_CACHE: dict[str, tuple[float, str, str, bool]] = {}


class PageError(ValueError):
    """A rejected page request (bad name / outside the fence / missing)."""

    def __init__(self, msg: str, status: int = 400) -> None:
        super().__init__(msg)
        self.status = status


# ── inline layer (input already HTML-escaped except code-span contents) ──────────

_CODE_SPAN = re.compile(r"`([^`]+)`")
_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITAL = re.compile(r"\*([^*]+)\*")


def _link_html(text: str, target: str) -> str:
    if re.match(r"^https?://", target):
        return f'<a href="{target}" target="_blank" rel="noopener">{text}</a>'
    base = target.split("#", 1)[0]
    if _NAME_RE.match(base) and base.endswith(".md"):
        return f'<a href="#" data-page="{base}">{text}</a>'
    return f'<span class="deadlink" title="lives in the repo: {target}">{text}</span>'


def _inline_nocode(esc: str) -> str:
    esc = _MD_LINK.sub(lambda mm: _link_html(mm.group(1), mm.group(2)), esc)
    esc = _BOLD.sub(r"<strong>\1</strong>", esc)
    esc = _ITAL.sub(r"<em>\1</em>", esc)
    return esc


def _inline(raw: str) -> str:
    # Code spans are split out FIRST so their contents are protected from every
    # other inline rule; each segment is escaped independently.
    parts = _CODE_SPAN.split(raw)
    out: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            out.append(f"<code>{html.escape(part)}</code>")
        else:
            out.append(_inline_nocode(html.escape(part)))
    return "".join(out)


# ── block layer ──────────────────────────────────────────────────────────────────

_H = re.compile(r"^(#{1,4})\s+(.*)$")
_HR = re.compile(r"^(---+|\*\*\*+|___+)\s*$")
_OL = re.compile(r"^\d+\.\s+(.*)$")
_TABLE_SEP = re.compile(r"^\|[\s:|-]+\|$")


def _row_cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def page_title(text: str) -> str:
    """The page's first h1, stripped of inline markup — '' if none."""
    for line in text.split("\n"):
        m = _H.match(line.strip())
        if m and len(m.group(1)) == 1:
            return re.sub(r"[*`]", "", m.group(2)).strip()
    return ""


def render(text: str) -> str:  # noqa: C901 — one deliberate state machine, kept in one place
    lines = text.split("\n")
    out: list[str] = []
    para: list[str] = []
    i, n = 0, len(lines)

    def flush_para() -> None:
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()

    def take_items(matcher) -> list[str]:
        """Collect list items; indented wrap-continuation lines join their item."""
        nonlocal i
        items: list[str] = []
        while i < n:
            s = lines[i].strip()
            got = matcher(s)
            if got is not None:
                items.append(got)
                i += 1
            elif items and s and lines[i].startswith(("  ", "\t")):
                items[-1] += " " + s
                i += 1
            else:
                break
        return items

    while i < n:
        stripped = lines[i].strip()

        if stripped.startswith("```"):  # fenced code — verbatim, escaped, no inline
            flush_para()
            i += 1
            code: list[str] = []
            while i < n and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1  # closing fence (or EOF)
            out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
            continue

        if not stripped:
            flush_para()
            i += 1
            continue

        m = _H.match(stripped)
        if m:
            flush_para()
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>" + _inline(m.group(2)) + f"</h{lvl}>")
            i += 1
            continue

        if _HR.match(stripped):
            flush_para()
            out.append("<hr>")
            i += 1
            continue

        if stripped.startswith(">"):
            flush_para()
            quote: list[str] = []
            while i < n and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip()[1:].lstrip())
                i += 1
            paras: list[list[str]] = [[]]
            for q in quote:  # blank '>' lines split paragraphs inside the quote
                if q:
                    paras[-1].append(q)
                elif paras[-1]:
                    paras.append([])
            body = "".join("<p>" + _inline(" ".join(p)) + "</p>" for p in paras if p)
            out.append("<blockquote>" + body + "</blockquote>")
            continue

        if stripped.startswith("|") and i + 1 < n and _TABLE_SEP.match(lines[i + 1].strip()):
            flush_para()
            header = _row_cells(stripped)
            i += 2
            rows: list[list[str]] = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append(_row_cells(lines[i].strip()))
                i += 1
            thead = "".join(f"<th>{_inline(c)}</th>" for c in header)
            tbody = "".join(
                "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in rows
            )
            out.append(
                '<div class="tscroll"><table><thead><tr>' + thead
                + "</tr></thead><tbody>" + tbody + "</tbody></table></div>"
            )
            continue

        if stripped.startswith(("- ", "* ")):
            flush_para()
            items = take_items(lambda s: s[2:] if s.startswith(("- ", "* ")) else None)
            out.append("<ul>" + "".join(f"<li>{_inline(it)}</li>" for it in items) + "</ul>")
            continue

        if _OL.match(stripped):
            flush_para()
            items = take_items(lambda s: m2.group(1) if (m2 := _OL.match(s)) else None)
            out.append("<ol>" + "".join(f"<li>{_inline(it)}</li>" for it in items) + "</ol>")
            continue

        para.append(stripped)
        i += 1

    flush_para()
    return "\n".join(out)


# ── pages (fence + cache) ────────────────────────────────────────────────────────

def _page_path(name: str) -> Path:
    """Validate + resolve a page name INSIDE the fence, or raise PageError."""
    if not name or not _NAME_RE.match(name) or not name.endswith(".md"):
        raise PageError(f"invalid page name: {name!r}")
    p = (_MANUAL_DIR / name).resolve()
    if p.parent != _MANUAL_DIR.resolve():
        raise PageError(f"outside the manual fence: {name!r}")
    if not p.is_file():
        raise PageError(f"no such page: {name!r}", status=404)
    return p


def load_page(name: str) -> dict:
    """Render (or serve the mtime-cached) page. Fail-soft: renderer errors degrade
    to escaped raw text rather than a broken pane."""
    p = _page_path(name)
    mtime = p.stat().st_mtime
    cached = _CACHE.get(name)
    if cached and cached[0] == mtime:
        _, title, body, degraded = cached
    else:
        text = p.read_text(encoding="utf-8")
        title = page_title(text) or name
        try:
            body, degraded = render(text), False
        except Exception as exc:  # noqa: BLE001 — fail-soft to raw
            logger.warning("manual: render failed for {} ({}) — serving raw",
                           name, type(exc).__name__)
            body, degraded = "<pre>" + html.escape(text) + "</pre>", True
        _CACHE[name] = (mtime, title, body, degraded)
    res = {"ok": True, "name": name, "title": title, "html": body}
    if degraded:
        res["degraded"] = True
    return res


def list_pages() -> list[dict]:
    """All manual pages with parsed titles; README.md pinned first, rest alphabetical."""
    pages = []
    for p in sorted(_MANUAL_DIR.glob("*.md")):
        try:
            title = page_title(p.read_text(encoding="utf-8")) or p.name
        except Exception:  # an unreadable page is still listed, by filename
            title = p.name
        pages.append({"name": p.name, "title": title})
    pages.sort(key=lambda e: e["name"] != "README.md")  # stable → README first
    return pages


# ── the seam contributor ────────────────────────────────────────────────────────

@register
def manual_routes(ctx: PanelContext) -> web.RouteTableDef:  # noqa: ARG001 — ctx unused by design
    """GET /manual/index + /manual/page/{name}. `ctx` unused: read-only file serving,
    fully decoupled from the live pipeline."""
    routes = web.RouteTableDef()

    @routes.get("/manual/index")
    async def manual_index(_req: web.Request) -> web.Response:
        try:
            return web.json_response({"ok": True, "pages": list_pages()})
        except Exception as exc:  # noqa: BLE001
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    @routes.get("/manual/page/{name}")
    async def manual_page(req: web.Request) -> web.Response:
        try:
            return web.json_response(load_page(req.match_info["name"]))
        except PageError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=exc.status)
        except Exception as exc:  # noqa: BLE001
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    return routes
