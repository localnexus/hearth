"""bot.py's build_pipeline → main handoff, checked statically.

main() cannot be unit-tested: it needs a mic, an LLM server and a loaded TTS
model. So the seam between build_pipeline() and main() — a positional tuple
with no names on it — has no runtime cover at all, and a mismatch there is
invisible until a real launch.

That is not hypothetical. af6527f added `voice_prefetch_built=memory_prefetch_proc`
to main() while memory_prefetch_proc stayed a local of build_pipeline, which was
never returned. Every facade-started bot then died at startup with a NameError
for a day, unnoticed, because the desk launch path ran an older tree that did
not carry the commit.

These are cheap AST checks, not behaviour tests. They catch the one failure
mode a positional handoff has: the two ends disagreeing.

Run:  .venv/bin/python -m unittest discover -s tests
"""

from __future__ import annotations

import ast
import builtins
import unittest
from pathlib import Path

import hearth.pipeline.bot as bot_mod

SOURCE = Path(bot_mod.__file__)
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))


def _func(name: str):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name}() not found in {SOURCE.name}")


class PipelineHandoff(unittest.TestCase):

    def test_return_arity_matches_the_unpacking(self):
        """The tuple build_pipeline returns must be the tuple main unpacks."""
        returned = None
        for node in ast.walk(_func("build_pipeline")):
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple):
                returned = [n.id for n in node.value.elts if isinstance(n, ast.Name)]
        self.assertIsNotNone(returned, "build_pipeline no longer returns a tuple")

        unpacked = None
        for node in ast.walk(_func("main")):
            if (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Tuple)
                    and isinstance(node.value, ast.Await)
                    and isinstance(node.value.value, ast.Call)
                    and getattr(node.value.value.func, "id", "") == "build_pipeline"):
                unpacked = [n.id for n in node.targets[0].elts if isinstance(n, ast.Name)]
        self.assertIsNotNone(unpacked, "main no longer unpacks build_pipeline")

        self.assertEqual(
            returned, unpacked,
            "build_pipeline's return tuple and main's unpacking have drifted — "
            "a positional handoff, so a mismatch is a startup crash, not a test failure")

    def test_main_references_no_undefined_names(self):
        """The NameError class of bug: a name main uses that nothing binds.

        Scoped deliberately narrow — only bare Name loads inside main(), checked
        against main's own bindings, the module's top level, and builtins.
        """
        main = _func("main")
        bound: set[str] = set()

        def add_args(a: ast.arguments) -> None:
            for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg]:
                if arg is not None:
                    bound.add(arg.arg)

        # ast.walk descends into nested defs, so their parameters count as bound
        # too — otherwise every closure argument reads as undefined.
        add_args(main.args)
        for node in ast.walk(main):
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store,)):
                bound.add(node.id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                bound.add(node.name)
                add_args(node.args)
            elif isinstance(node, ast.Lambda):
                add_args(node.args)
            elif isinstance(node, ast.ClassDef):
                bound.add(node.name)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bound.add(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    bound.add((alias.asname or alias.name).split(".")[0])

        module_level = set(dir(bot_mod)) | set(dir(builtins))
        undefined = sorted({
            node.id for node in ast.walk(main)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
            and node.id not in bound and node.id not in module_level
        })
        self.assertEqual(undefined, [],
                         f"main() reads names nothing binds: {undefined}")


if __name__ == "__main__":
    unittest.main()
