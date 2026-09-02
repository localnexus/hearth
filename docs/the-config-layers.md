# The config layers — who writes what

**This page has a single canonical home: the in-app manual**, served by the control panel
under `/manual` and kept in step with the shipped code. Read it here:

**→ [The config layers (canonical)](../src/hearth/config/users-manual/the-config-layers.md)**

It covers the two roots (`HEARTH_ROOT` / `HEARTH_DATA`), who owns each config layer —
`active.toml` (you), the panel's `overrides.toml` (hands off), the model dirs (you), the
shipped `tts.toml` / `vad.toml` baselines, `serve.toml` (manage, never print), and the
memory / OpenClaw gates — and which changes are hot versus need a restart.

*Why the redirect: this content used to live in two files and drifted. The panel-served copy
under `src/hearth/config/users-manual/` is now the one source of truth, and this page points
to it so the two can't diverge again.*
