"""test_manual.py — features/manual.py (renderer subset + fence + index).

Runnable directly (repo convention — no pytest in venv):

    uv run python test_manual.py

Covers: every construct the nine pages use (the proposal's content survey), escaping
(NO raw-HTML passthrough), the link policy (in-scope / out-of-scope / external), the
scope fence (name/traversal guards), fail-soft degrade, an index + render pass over
the REAL pages (cache stability included), and seam registration. Does NOT start the
web server — the route handlers are thin shells over these pure functions.
"""

from hearth.control import control_routes
from hearth.control.features import manual as m
from hearth.control.features.manual import PageError, page_title, render

_PASS = 0
_FAIL = 0


def check(name: str, cond: bool) -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ok   {name}")
    else:
        _FAIL += 1
        print(f"  FAIL {name}")


def rejects(name: str, fn, status: int | None = None) -> None:
    """Assert fn() raises PageError (optionally with a specific HTTP status)."""
    try:
        fn()
    except PageError as exc:
        ok = status is None or exc.status == status
        check(name if ok else f"{name} (status {exc.status}, wanted {status})", ok)
    except Exception as exc:  # wrong exception type
        check(f"{name} (raised {type(exc).__name__}, wanted PageError)", False)
    else:
        check(f"{name} (did not raise)", False)


def test_blocks():
    print("test_blocks")
    h = render("# Title\n\n## Sub\n\ntext line\njoined here\n")
    check("h1", "<h1>Title</h1>" in h)
    check("h2", "<h2>Sub</h2>" in h)
    check("paragraph joins wrapped lines", "<p>text line joined here</p>" in h)
    check("hr", "<hr>" in render("a\n\n---\n\nb"))
    h = render("```\ncode **not bold** <tag>\n```")
    check("fence escapes, no inline",
          "<pre><code>code **not bold** &lt;tag&gt;</code></pre>" in h)
    h = render("> quoted **bold**\n> second line")
    check("blockquote + inline", "<blockquote>" in h and "<strong>bold</strong>" in h)
    check("blockquote joins lines", "second line" in h and h.count("<p>") == 1)
    h = render("- one\n- two **b**\n")
    check("ul", h.count("<li>") == 2 and "<strong>b</strong>" in h)
    h = render("1. first\n2. second\n")
    check("ol", "<ol>" in h and h.count("<li>") == 2)
    h = render("- a long item\n  that wraps\n- next\n")
    check("list wrap-continuation absorbed", "<li>a long item that wraps</li>" in h)
    h = render("| A | B |\n|---|---|\n| **x** | `y` |\n")
    check("table head", "<th>A</th>" in h)
    check("table cells keep inline", "<td><strong>x</strong></td>" in h and "<code>y</code>" in h)
    check("table gets scroll wrap", '<div class="tscroll">' in h)
    check("title parse", page_title("# A *t* — x\nbody") == "A t — x")


def test_inline_and_escaping():
    print("test_inline_and_escaping")
    h = render("**bold** and *ital* and `code`")
    check("bold", "<strong>bold</strong>" in h)
    check("ital", "<em>ital</em>" in h)
    check("code span", "<code>code</code>" in h)
    h = render("<script>alert(1)</script>")
    check("raw html escaped (no passthrough)", "<script" not in h and "&lt;script&gt;" in h)
    h = render("`<b>no</b>`")
    check("html inside code span escaped", "<b>" not in h)
    h = render("a `code **kept literal**` b")
    check("inline rules skip code spans", "<code>code **kept literal**</code>" in h)


def test_link_policy():
    print("test_link_policy")
    h = render("[next](when-it-misbehaves.md)")
    check("in-scope → data-page anchor", 'data-page="when-it-misbehaves.md"' in h)
    h = render("[out](../RUNBOOK.md)")
    check("out-of-scope → inert deadlink",
          'class="deadlink"' in h and "lives in the repo" in h and "<a" not in h)
    h = render("[site](https://example.com/x)")
    check("external → new-tab anchor",
          'target="_blank"' in h and 'href="https://example.com/x"' in h)


def test_fence():
    print("test_fence")
    rejects("reject traversal", lambda: m._page_path("../config.toml"))
    rejects("reject absolute path", lambda: m._page_path("/etc/hosts"))
    rejects("reject non-md", lambda: m._page_path("README.txt"))
    rejects("reject empty name", lambda: m._page_path(""))
    rejects("missing page → 404", lambda: m._page_path("no-such-page.md"), status=404)
    check("real page resolves inside fence", m._page_path("README.md").name == "README.md")


def test_real_pages():
    print("test_real_pages")
    pages = m.list_pages()
    check("fourteen pages listed", len(pages) == 14)
    check("README pinned first", pages[0]["name"] == "README.md")
    check("every title parsed (no filename fallbacks)",
          all(p["title"] and p["title"] != p["name"] for p in pages))
    bad = []
    for p in pages:
        res = m.load_page(p["name"])
        if not res["ok"] or res.get("degraded"):
            bad.append(p["name"])
        if m.load_page(p["name"])["html"] != res["html"]:
            bad.append(p["name"] + " (cache unstable)")
    check("every real page renders un-degraded (+mtime cache stable)", not bad)


def test_seam_registration():
    print("test_seam_registration")
    names = [fn.__name__ for fn in control_routes.contributors()]
    check("manual_routes registered", "manual_routes" in names)


if __name__ == "__main__":
    test_blocks()
    test_inline_and_escaping()
    test_link_policy()
    test_fence()
    test_real_pages()
    test_seam_registration()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    raise SystemExit(1 if _FAIL else 0)
