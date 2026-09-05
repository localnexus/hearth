# The plain-words check (`manual-lint`)

<!-- manual-lint: allow-file — this page's subject is the words themselves -->

Hearth's prose uses plain words for things a stranger has never heard named:
**Hearth** (not facade or daemon), **access key** (not bearer), **companion** (not
bot), **model server** (not LLM server), **data folder** (not data root). The
technical name is fine in *identifier position* — a config key, a module path, a
route, a flag, a code span — and is introduced once, in the config-layers chapter.

That rule was applied by hand once. `manual-lint` is the standing version: it walks
every surface a reader sees and reports each internal name that appears in prose,
with the plain word to use.

## Run it

```
.venv/bin/python -m hearth.tools.manual_lint            # every finding
.venv/bin/python -m hearth.tools.manual_lint --summary  # per-file counts
.venv/bin/python -m hearth.tools.manual_lint --words    # the word list
.venv/bin/python -m hearth.tools.manual_lint docs/installing.md src/hearth/ui
```

Output is `path:line: [word] 'context' — plain word: …`, one line per finding, then
a `CLEAN` / `WARN` summary. Run bare it is warning-level: exit 0 whatever it
finds. `--strict` exits 1 on findings, and that is how the pre-commit hook runs it.

## What it reads

| Surface | Files | What counts as prose |
|---|---|---|
| Markdown | `docs/`, the in-app users-manual, `README.md` | everything outside fenced, indented and inline code; link targets skipped |
| Page HTML | `src/hearth/**/*.html` | text between tags; inline scripts as JS; HTML comments exempt |
| JS | `src/hearth/ui/*.js` | string literals that contain a space (a bare name is not prose) |
| Python | `init/`, `supervisor/`, `serve/` | string literals with a space — the JSON replies, prints and page text a page shows verbatim |

Exempt by design: docstrings, comments, and every `logger.*` argument. Only the
operator reads those. A word glued to path punctuation (`hearth.pipeline.bot`,
`/admin/bot/start`, `--memory`) is a name and passes; a sentence-ending period is not
glue. A line carrying `manual-lint: allow` is never flagged; a file carrying
`manual-lint: allow-file` is skipped whole. Use them where the technical word is
the subject, as in a glossary entry or this page.

## Where it runs

The pre-commit hook runs it with `--strict` after the scrub gate, so a new
finding blocks the commit (a pilot since 2026-09-05, once the whole corpus was
clean). Use the plain word, or where the technical name is the right one, put
`manual-lint: allow` on the line (or `allow-file` at the top). The mark is in
the diff, so an exception is a visible decision. Relaxing it is one flag.
