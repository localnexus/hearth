"""The brand layer is ONE palette and ONE header, served by six pages.

The control panel (:65000) and the five facade pages (:65001) had drifted into
two visual languages — the panel a committed dark console with an ember palette
and an inlined mark, the facade pages theme-adaptive chrome with no brand at
all. ui/brand.css and ui/brand.py are now the single source, spliced into every
page at import, with the artwork served from /ui/brand/ instead of inlined.

These are DIVERGENCE GUARDS, the same idiom as test_shared_switch_card: they do
not test how anything looks, only that the shared thing is still shared, so the
next palette change lands in one file instead of six.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from hearth.control import control as control_mod
from hearth.supervisor import curation as curation_mod
from hearth.supervisor import firstrun as firstrun_mod
from hearth.supervisor import roster as roster_mod
from hearth.supervisor import routes as routes_mod
from hearth.supervisor import settings as settings_mod
from hearth.ui import brand

#: name → the page as it is actually served (post-splice).
PAGES = {
    "control": control_mod._HTML(),
    "launch": routes_mod._LAUNCH_PAGE(),
    "firstrun": firstrun_mod._PAGE(),
    "pair": routes_mod._PAIR_PAGE(),
    "roster": roster_mod._PAGE(),
    "settings": settings_mod._PAGE(),
    "memory": curation_mod._PAGE(),
}

#: The facade pages follow the OS; the panel is committed dark and must not.
ADAPTIVE = ("launch", "firstrun", "pair", "roster", "settings", "memory")

# Declaring any of these locally means someone forked the palette. Note these
# match DECLARATIONS, not uses: a page saying color:var(--ember) is consuming
# the shared token correctly, and a host-specific override that scopes the
# shared class (the panel's `#rail .brandmark`) is a size tweak, not a fork.
OWNED_BY_BRAND = (
    re.compile(r"--ember(-hi)?\s*:"),
    re.compile(r"--brand-(ink|sub|rule)\s*:"),
    re.compile(r"--glow\s*:"),
    re.compile(r"(?m)^\s*\.brandhead\b[^\n{]*\{"),
    re.compile(r"(?m)^\s*\.brandsub\b[^\n{]*\{"),
    re.compile(r"(?m)^\s*\.brandmark\b[^\n{]*\{"),
)


class SharedBrand(unittest.TestCase):

    def test_every_page_gets_the_brand_css(self):
        """A page still holding the placeholder renders unbranded and unthemed."""
        for name, page in PAGES.items():
            with self.subTest(page=name):
                self.assertNotIn(brand.PLACEHOLDER, page,
                                 "placeholder was never replaced")
                self.assertIn("--ember-hi", page, "palette missing")
                self.assertIn(".brandmark", page, "mark styling missing")

    def test_the_css_appears_exactly_once_per_page(self):
        """Two copies would mean two palettes racing on specificity."""
        marker = "--brand-ink: var(--ember-hi)"
        for name, page in PAGES.items():
            with self.subTest(page=name):
                self.assertEqual(page.count(marker), 1)

    def test_no_page_redefines_what_the_brand_layer_owns(self):
        """The palette lives in brand.css. A page that re-declares a brand token
        has forked it, and the two will drift the way the panel and the facade
        already did once."""
        css = brand.CSS
        for name, page in PAGES.items():
            body = page.replace(css, "")  # everything EXCEPT the shared layer
            for pattern in OWNED_BY_BRAND:
                with self.subTest(page=name, pattern=pattern.pattern):
                    found = pattern.search(body)
                    if found is not None:
                        self.fail(f"{name} declares {found.group(0)!r} itself — "
                                  "put it in ui/brand.css instead")

    def test_the_header_is_built_once_not_written_six_times(self):
        """Every page names its section; the markup around it comes from
        brand.header, so six pages cannot grow six header structures."""
        for name, page in PAGES.items():
            with self.subTest(page=name):
                self.assertNotIn("<!--BRANDHEAD:", page,
                                 "header placeholder was never expanded")
                self.assertIn('<header class="brandhead">', page)
                self.assertIn('<div class="brandsub">', page)
                self.assertEqual(page.count('<header class="brandhead">'), 1)

    def test_only_the_adaptive_hosts_opt_into_light_mode(self):
        """The facade follows the OS (it is the phone surface); the panel is
        committed dark, and taking the light overrides would leave its wordmark
        a dark brown on charcoal."""
        for name, page in PAGES.items():
            with self.subTest(page=name):
                opted_in = 'class="brand-adaptive"' in page
                self.assertEqual(opted_in, name in ADAPTIVE)

    def test_artwork_is_referenced_not_inlined(self):
        """12.7 KB of base64 in six pages is what this replaced; an inline
        data: URI creeping back would undo it silently."""
        for name, page in PAGES.items():
            with self.subTest(page=name):
                self.assertNotIn("data:image/png;base64", page)
        self.assertIn("/ui/brand/favicon.png", PAGES["control"])
        for name in ADAPTIVE:
            with self.subTest(page=name):
                self.assertIn("/ui/brand/favicon.png", PAGES[name],
                              "page has no favicon")

    def test_the_assets_are_real_pngs_read_from_disk(self):
        for name, blob in brand.ASSETS.items():
            with self.subTest(asset=name):
                self.assertTrue(blob.startswith(b"\x89PNG\r\n\x1a\n"))
                on_disk = (Path(brand.__file__).parent / "brand" / name).read_bytes()
                self.assertEqual(blob, on_disk)

    def test_routes_match_the_assets(self):
        self.assertEqual(sorted(brand.ROUTES),
                         sorted(f"/ui/brand/{n}" for n in brand.ASSETS))

    def test_splice_refuses_a_page_without_the_placeholder(self):
        """Silently serving an unbranded page is the failure this prevents."""
        with self.assertRaises(ValueError):
            brand.splice("<html><style></style></html>")

    def test_header_carries_the_section_label(self):
        html = brand.header("roster")
        self.assertIn(">roster<", html)
        self.assertIn("<h1>Hearth</h1>", html)


if __name__ == "__main__":
    unittest.main()
