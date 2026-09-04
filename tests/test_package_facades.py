"""Every split package keeps its façade complete.

The split packages under `config/` and `supervisor/` were each one module until
the file-size queue split them. Each promised the same thing: the import path
does not change and neither does the surface, because the package `__init__`
re-exports every name its parts define — the underscored ones included, since
callers and tests reach for those by name (`sr._model_of`, `roster._PAGE`).

That promise is easy to keep on the day of the split and easy to break the
next time someone adds a helper to a part and forgets the re-export. The
failure is quiet: the name simply isn't there any more, and only whichever
caller wanted it finds out. So it is checked here, by reading what each part
actually defines rather than by trusting a list.

A part may deliberately keep something to itself — add it to EXEMPT below with
the reason, so not-exported stays a decision rather than an oversight.
"""

import ast
import importlib
import unittest
from pathlib import Path

PACKAGES = ("hearth.config.settings_registry", "hearth.supervisor.roster",
            "hearth.supervisor.routes")

#: (package, name) pairs that are deliberately NOT on the façade.
EXEMPT: set[tuple[str, str]] = set()


def _defined_at_top_level(path: Path) -> set[str]:
    """Names the module binds at module level — defs, classes, assignments.

    Imports are excluded on purpose: a part importing `shutil` says nothing
    about what the package should export.
    """
    out: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            out |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return {n for n in out if not n.startswith("__")}


class TheFacadeReExportsEveryPart(unittest.TestCase):

    def test_every_name_a_part_defines_is_on_the_package(self):
        for dotted in PACKAGES:
            pkg = importlib.import_module(dotted)
            directory = Path(pkg.__file__).parent
            parts = sorted(p for p in directory.glob("*.py")
                           if p.name != "__init__.py")
            self.assertTrue(parts, f"{dotted} has no part modules")
            for part in parts:
                for name in sorted(_defined_at_top_level(part)):
                    if (dotted, name) in EXEMPT:
                        continue
                    with self.subTest(package=dotted, part=part.name, name=name):
                        self.assertTrue(
                            hasattr(pkg, name),
                            f"{part.name} defines {name!r} but "
                            f"{dotted} does not re-export it")

    def test_the_parts_import_in_one_direction(self):
        """Each part imports only parts listed EARLIER in `__init__`'s import
        block, which each package writes in dependency order.

        The strong half is that a cycle cannot form. The rest is bookkeeping
        worth keeping: the façade's import block doubles as the reading order,
        and a part that starts importing downward means the layout note above
        it has quietly stopped being true."""
        for dotted in PACKAGES:
            pkg = importlib.import_module(dotted)
            directory = Path(pkg.__file__).parent
            init = ast.parse((directory / "__init__.py").read_text(encoding="utf-8"))
            order = [n.module for n in ast.walk(init)
                     if isinstance(n, ast.ImportFrom) and n.level == 1 and n.module]
            for i, part in enumerate(order):
                tree = ast.parse((directory / f"{part}.py").read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.level == 1:
                        with self.subTest(package=dotted, part=part,
                                          imports=node.module):
                            self.assertIn(node.module, order[:i],
                                          f"{part} imports {node.module}, which "
                                          "is not earlier in the package order")


if __name__ == "__main__":
    unittest.main()
