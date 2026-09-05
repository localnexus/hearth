"""hearth.init — the first-run bootstrap: a bare checkout → a running facade
with a door open.

Every friendly surface Hearth ships — the companion switcher, the launch page,
the settings forms, memory — sits behind gates that ship OFF, and rightly so:
the facade opens a socket and needs a minted secret; memory writes durable
records about a person. Before this module the way through those gates was
four uncommented decisions in files the install guide called optional. This
is the one deliberate path that opens them, for someone who has never seen
Hearth (the audience it was built for, signed 2026-09-04).

What it does, in order — each step idempotent and reported:
  1. copy active.toml, models/example/model.toml, serve.toml from their
     .example templates into the DATA root. Copy-on-write: an existing file is
     left alone and named as such. The templates are never edited.
  2. mint the bearer at serve.toml's token_source (0600, created exclusively).
  3. set [serve] enabled and [serve.supervisor] enabled by the same
     comment-preserving line surgery the settings forms use — aim, then
     parse-verify against the intended document, refuse on any difference.
  4. memory: only when asked for. Copies memory.toml and sets enabled.
  5. the LLM: record the base URL, and when the server answers, offer the ids
     it advertises for model.toml's `id`.

It reports paths and states, never content, and prints a secret exactly once:
the token at the moment it is minted (the launch page asks for it next). A
re-run says where the token lives and stops there.
"""

from __future__ import annotations

import copy
import json
import os
import secrets
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from hearth.config import config_loader as cl
from hearth.config import settings_registry as sr
from hearth.supervisor.settings.surgery import _deep_get, _deep_set, _surgical_set

PLACEHOLDER_ID = "your-model-id-here"
DEFAULT_LM_URL = "http://127.0.0.1:8080/v1"
_TEMPLATES = (  # (relative path, registry kind) — copied ROOT → DATA
    ("config/active.toml", "active"),
    ("config/models/example/model.toml", "model"),
    ("config/serve.toml", "serve"),
)
_MEMORY_TEMPLATE = ("config/memory.toml", "memory")


class InitError(RuntimeError):
    """Something the bootstrap will not guess at. The message names the file;
    the file is left byte-identical."""


@dataclass
class Report:
    """What happened, one line per step: (state, text). States are a small
    fixed vocabulary so the caller can colour them and tests can assert on
    them without parsing prose."""
    lines: list[tuple[str, str]] = field(default_factory=list)
    token: str | None = None  # set ONLY when minted this run

    def add(self, state: str, text: str) -> None:
        self.lines.append((state, text))

    def states(self) -> list[str]:
        return [s for s, _ in self.lines]


def _rel(path: Path) -> str:
    """A path as the operator will recognise it: relative to the data root."""
    try:
        return str(path.relative_to(cl.DATA_DIR))
    except ValueError:
        return str(path)


# ── 1. templates ──────────────────────────────────────────────────────────────

def copy_templates(rep: Report) -> dict[str, Path]:
    """Each shipped .example → its live place under DATA, never overwriting."""
    out: dict[str, Path] = {}
    for rel, kind in _TEMPLATES:
        out[kind] = _copy_one(rel, rep)
    return out


def _copy_one(rel: str, rep: Report) -> Path:
    src = cl._ROOT / (rel + ".example")
    dst = cl.DATA_DIR / rel
    if dst.is_file():
        rep.add("exists", f"{_rel(dst)} — left as it is")
        return dst
    if not src.is_file():
        raise InitError(f"template missing from the engine tree: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())
    rep.add("created", f"{_rel(dst)} (from {src.name})")
    return dst


# ── 2. the token ──────────────────────────────────────────────────────────────

def mint_token(serve_path: Path, rep: Report) -> Path:
    """Create the bearer file serve.toml points at, exclusively and 0600. An
    existing file is kept and NOT read back — its value is never printed twice."""
    doc = _parse(serve_path)
    src = str(_deep_get(doc, ["serve", "token_source"], "") or "config/serve-token")
    path = cl.resolve_data_path(src)
    if path.is_file():
        rep.add("exists", f"access key at {_rel(path)} (not shown again)")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(token + "\n")
    rep.token = token
    rep.add("created", f"access key at {_rel(path)} (readable only by you)")
    return path


# ── 3. gates, by line surgery ─────────────────────────────────────────────────

def set_key(path: Path, kind: str, full: list[str], value, rep: Report,
            label: str | None = None) -> bool:
    """Set one key in a gate/config file, touching nothing else. `full` is the
    key path from the document root (["serve", "supervisor", "enabled"]). The
    edit is parse-verified against the intended document and strict-checked
    for NEW schema errors; either failure refuses and leaves the file as it was.
    Returns True when the file changed."""
    label = label or ".".join(full)
    text = path.read_text(encoding="utf-8")
    doc = _parse(path, text)
    if _deep_get(doc, full, None) == value:
        rep.add("unchanged", f"{_rel(path)}: {label} already {_render(value)}")
        return False
    expected = copy.deepcopy(doc)
    _deep_set(expected, full, value)
    section = ".".join(full[:-1])
    new_text = _surgical_set(text, section, full[-1], _render(value))
    try:
        got = tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as exc:
        raise InitError(f"{_rel(path)}: setting {label} would not parse ({exc}) — "
                        "the file is untouched; set it by hand") from None
    if got != expected:
        raise InitError(f"{_rel(path)}: setting {label} changed more than one key — "
                        "the file is untouched; set it by hand")
    entry = sr.REGISTRY[kind]
    inner_old = doc.get(entry.top_key, {}) if entry.top_key else doc
    inner_new = got.get(entry.top_key, {}) if entry.top_key else got
    before, _ = sr.strict_check(kind, inner_old if isinstance(inner_old, dict) else {})
    after, _ = sr.strict_check(kind, inner_new if isinstance(inner_new, dict) else {})
    new_errs = [e for e in after if e not in before]
    if new_errs:
        raise InitError(f"{_rel(path)}: setting {label} would fail the schema "
                        f"({new_errs[0]}) — the file is untouched")
    path.write_text(new_text, encoding="utf-8")
    rep.add("set", f"{_rel(path)}: {label} = {_render(value)}")
    return True


def open_gates(serve_path: Path, rep: Report) -> None:
    """The two switches every friendly surface waits on. The supervisor table
    ships commented out, so surgery appends a real one rather than uncommenting
    — the template's own text stays as the reader found it."""
    set_key(serve_path, "serve", ["serve", "enabled"], True, rep)
    set_key(serve_path, "serve", ["serve", "supervisor", "enabled"], True, rep)


def set_lm_url(serve_path: Path, url: str, rep: Report) -> None:
    set_key(serve_path, "serve", ["serve", "lm_base_url"], url, rep)


# ── 4. memory, only when asked ────────────────────────────────────────────────

def enable_memory(rep: Report) -> Path:
    rel, kind = _MEMORY_TEMPLATE
    path = _copy_one(rel, rep)
    set_key(path, kind, ["memory", "enabled"], True, rep)
    return path


# ── 5. the LLM server ─────────────────────────────────────────────────────────

def probe_models(url: str, token: str = "", timeout: float = 4.0) -> list[str] | None:
    """The ids an OpenAI-compatible server advertises, or None when nothing
    answers. Reads the `data` key (what llama-server, LM Studio and the
    facade itself all emit)."""
    req = urllib.request.Request(url.rstrip("/") + "/models")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.load(resp)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    items = body.get("data") if isinstance(body, dict) else None
    if not isinstance(items, list):
        return None
    return [str(m.get("id")) for m in items if isinstance(m, dict) and m.get("id")]


def current_model_id(model_path: Path) -> str:
    return str(_parse(model_path).get("id", ""))


def set_model_id(model_path: Path, model_id: str, rep: Report) -> None:
    set_key(model_path, "model", ["id"], model_id, rep, label="id")


# ── the door, for the closing message ────────────────────────────────────────

def facade_url(serve_path: Path) -> str:
    doc = _parse(serve_path).get("serve", {})
    host = doc.get("host", "127.0.0.1")
    port = doc.get("port", 65001)
    return f"http://{host}:{port}/admin/launch"


# ── helpers ───────────────────────────────────────────────────────────────────

def _parse(path: Path, text: str | None = None) -> dict:
    try:
        return tomllib.loads(text if text is not None else path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise InitError(f"{_rel(path)}: unreadable ({type(exc).__name__})") from None
    except tomllib.TOMLDecodeError as exc:
        raise InitError(f"{_rel(path)}: does not parse ({exc}) — fix it by hand, "
                        "then run init again") from None


def _render(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)
    return str(value)
