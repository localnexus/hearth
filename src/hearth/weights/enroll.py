"""weights/enroll.py — binding one set of weights to one model directory.

Enrolling writes a REFERENCE into `config/models/<name>/model.toml` and touches
nothing else on the machine. The file stays exactly where its owner put it;
un-enrolling deletes the reference and never the weights. Hearth does not
download, move, copy, or delete a weights file, ever.

Two rules the write obeys:

  * The target is always `MODELS_DIR/<name>/model.toml` under the DATA folder.
    The engine tree ships an `example` model directory to copy; it is never
    written into, so an install can always be reset by emptying the data folder.
  * The write is line surgery, the same idiom the first-run setup uses: the
    `[weights]` and `[weights.header]` blocks are replaced in place if they
    exist and appended if they do not. Every other line of the file — comments
    included, and a model.toml is mostly comments — comes through byte-identical.

`check()` reports state and never raises: weights that have been deleted or
moved out from under the door are an error FINDING with a plain message, never
an exception and never a silent fall-back to some product's copy (guard rail
R3). The door stays down and says why.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from hearth.config import config_loader as cl

from .header import HeaderFacts, identity as file_identity
from .scan import SHARD_RE, Candidate

#: The line written above the block, so a reader knows who wrote it and why.
COMMENT_MARK = "# [weights] enrolled by `python -m hearth.weights enroll` on "
COMMENT_TAIL = " — Hearth keeps a reference here, never the file"

#: `[weights]` / `[weights.header]` / any other table header.
_TABLE_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")

#: The order the keys are written in — reference first, provenance after.
KEY_ORDER = ("path", "mmproj", "root", "layout", "display_key", "size_bytes",
             "identity", "enrolled")


class WeightsError(RuntimeError):
    """Something enrollment will not guess at. The message names the file."""


@dataclass(frozen=True)
class EnrolledWeights:
    model_name: str
    path: Path
    root: str = ""
    layout: str = ""
    display_key: str = ""
    size_bytes: int = 0
    identity: str = ""
    enrolled: str = ""
    mmproj: Path | None = None
    header: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Finding:
    level: str   # ok | warn | error
    text: str


# ── reading ──────────────────────────────────────────────────────────────────

def model_toml(model_name: str) -> Path:
    """The file enrollment reads. DATA first, then the shipped example."""
    return cl.model_dir(model_name) / "model.toml"


def data_model_toml(model_name: str) -> Path:
    """The file enrollment WRITES — always under the data folder."""
    return cl.MODELS_DIR / model_name / "model.toml"


def load_enrolled(model_name: str) -> EnrolledWeights | None:
    """The `[weights]` table of one model directory, or None if it has none."""
    path = model_toml(model_name)
    if not path.is_file():
        return None
    try:
        data = cl._read_toml(path)
    except cl.ConfigError:
        raise
    table = data.get("weights")
    if not isinstance(table, dict) or not table.get("path"):
        return None
    mmproj = table.get("mmproj")
    header = table.get("header")
    return EnrolledWeights(
        model_name=model_name,
        path=Path(str(table["path"])),
        root=str(table.get("root", "")),
        layout=str(table.get("layout", "")),
        display_key=str(table.get("display_key", "")),
        size_bytes=int(table.get("size_bytes") or 0),
        identity=str(table.get("identity", "")),
        enrolled=str(table.get("enrolled", "")),
        mmproj=Path(str(mmproj)) if mmproj else None,
        header=dict(header) if isinstance(header, dict) else {},
    )


def enrolled_models() -> list[str]:
    """Every model directory under DATA whose model.toml carries a [weights]
    table, in name order."""
    out: list[str] = []
    if not cl.MODELS_DIR.is_dir():
        return out
    for entry in sorted(p for p in cl.MODELS_DIR.iterdir() if p.is_dir()):
        try:
            if load_enrolled(entry.name) is not None:
                out.append(entry.name)
        except cl.ConfigError:
            continue
    return out


# ── the write (line surgery, comments preserved) ─────────────────────────────

def _render(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _block_span(lines: list[str]) -> tuple[int, int] | None:
    """(start, end) line indices of the [weights] + [weights.header] blocks."""
    start = None
    for i, line in enumerate(lines):
        match = _TABLE_RE.match(line)
        if match is None:
            continue
        name = match.group(1).strip()
        if name == "weights" or name.startswith("weights."):
            if start is None:
                start = i
        elif start is not None:
            return start, i
    if start is None:
        return None
    return start, len(lines)


def _widen(lines: list[str], start: int) -> int:
    """Take the marker comment, and one blank line above it, with the block."""
    if start > 0 and lines[start - 1].startswith(COMMENT_MARK):
        start -= 1
    if start > 0 and lines[start - 1].strip() == "":
        start -= 1
    return start


def _block_lines(table: dict, when: str) -> list[str]:
    out = [f"{COMMENT_MARK}{when}{COMMENT_TAIL}", "[weights]"]
    for key in KEY_ORDER:
        if table.get(key) is None:
            continue
        out.append(f"{key} = {_render(table[key])}")
    header = table.get("header") or {}
    if header:
        out.append("")
        out.append("[weights.header]")
        for key, value in header.items():
            if value is not None:
                out.append(f"{key} = {_render(value)}")
    return out


def write_weights_table(model_toml_path: Path, table: dict,
                        when: str | None = None) -> None:
    """Replace the [weights] blocks in place, or append them. Nothing else moves."""
    model_toml_path = Path(model_toml_path)
    text = model_toml_path.read_text(encoding="utf-8")
    lines = text.split("\n")
    block = _block_lines(table, when or date.today().isoformat())
    span = _block_span(lines)
    if span is None:
        while lines and lines[-1].strip() == "":
            lines.pop()
        lines += [""] + block + [""]
    else:
        start, end = span
        lines[_widen(lines, start):end] = block
    model_toml_path.write_text("\n".join(lines), encoding="utf-8")


def remove_weights_table(model_toml_path: Path) -> bool:
    """Delete the blocks (and the marker comment) — every other line untouched."""
    model_toml_path = Path(model_toml_path)
    text = model_toml_path.read_text(encoding="utf-8")
    lines = text.split("\n")
    span = _block_span(lines)
    if span is None:
        return False
    start, end = span
    start = _widen(lines, start)
    del lines[start:end]
    if not lines or lines[-1] != "":
        lines.append("")
    model_toml_path.write_text("\n".join(lines), encoding="utf-8")
    return True


def enroll(model_name: str, candidate: Candidate,
           mmproj: Candidate | None = None) -> Path:
    """Bind `candidate` to `config/models/<model_name>/model.toml`. → the path."""
    target = data_model_toml(model_name)
    if not target.is_file():
        raise WeightsError(
            f"no model directory to enroll into: {target} does not exist. "
            f"Create it first — copy the shipped example "
            f"(config/models/example/model.toml.example) to "
            f"{cl.MODELS_DIR / model_name / 'model.toml'} and set its `id` — "
            "then enroll. Hearth never writes into the engine tree.")
    header = candidate.header.declared() if candidate.header else {}
    table = {
        "path": str(candidate.path),
        "mmproj": str(mmproj.path) if mmproj is not None else None,
        "root": candidate.root_name,
        "layout": candidate.layout,
        "display_key": candidate.display_key,
        "size_bytes": int(candidate.size_bytes),
        "identity": candidate.identity,
        "enrolled": date.today().isoformat(),
        "header": header,
    }
    write_weights_table(target, table)
    return target


def unenroll(model_name: str) -> Path | None:
    """Remove the reference. The weights file is never touched."""
    target = data_model_toml(model_name)
    if not target.is_file():
        return None
    return target if remove_weights_table(target) else None


# ── the door's own vocabulary ────────────────────────────────────────────────

_LONG_FLAG_RE = re.compile(r"--([A-Za-z0-9][A-Za-z0-9-]*)")
#: llama.cpp's preset vocabulary keys on the flag NAME, and a few of the ones a
#: model entry wants are the door's short names (`c` = --ctx-size). So the
#: accepted set is both: every long flag, plus every single-letter short flag.
_SHORT_FLAG_RE = re.compile(r"(?<![\w-])-([A-Za-z0-9])(?![\w-])")
_HELP_CACHE: dict[str, frozenset[str]] = {}


def server_long_flags(llama_server: str | None) -> frozenset[str] | None:
    """The flag names the door accepts, from its own --help. Once per process.

    This is Hearth's OWN binary — the thing it serves through — not a product,
    so asking it what it accepts is not the coupling R1 forbids.
    """
    if not llama_server:
        return None
    if llama_server in _HELP_CACHE:
        return _HELP_CACHE[llama_server]
    try:
        done = subprocess.run([llama_server, "--help"], capture_output=True,
                              text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    text = (done.stdout or "") + (done.stderr or "")
    flags = frozenset(_LONG_FLAG_RE.findall(text)) | frozenset(_SHORT_FLAG_RE.findall(text))
    if not flags:
        return None
    _HELP_CACHE[llama_server] = flags
    return flags


# ── check: report state, never raise, never fall back ────────────────────────

def _shard_siblings(path: Path) -> list[Path]:
    match = SHARD_RE.match(path.name)
    if match is None:
        return []
    base, total = match.group("base"), int(match.group("total"))
    return [path.parent / f"{base}-{i:05d}-of-{total:05d}.gguf"
            for i in range(1, total + 1)]


def check(model_name: str, llama_server: str | None = None) -> list[Finding]:
    """Everything that could have drifted since the day this was enrolled."""
    out: list[Finding] = []
    path = model_toml(model_name)
    if not path.is_file():
        return [Finding("error", f"{model_name}: no model.toml at {path}")]
    try:
        data = cl._read_toml(path)
    except cl.ConfigError as exc:
        return [Finding("error", f"{model_name}: {exc}")]

    out.append(Finding("ok", f"{model_name}: id = {data['id']!r}")
               if data.get("id") else
               Finding("error", f"{model_name}: no `id` key — the model.toml is incomplete"))

    enrolled = load_enrolled(model_name)
    if enrolled is None:
        out.append(Finding("warn", f"{model_name}: no [weights] table — "
                                   "nothing enrolled (`enroll` binds a file)"))
    else:
        weights = enrolled.path
        if not weights.is_file():
            out.append(Finding("error", f"{model_name}: weights missing — {weights} "
                                        "is not there any more"))
        else:
            out.append(Finding("ok", f"{model_name}: weights present — {weights}"))
            shards = _shard_siblings(weights)
            gone = [s for s in shards if not s.is_file()]
            if shards:
                out.append(Finding("error", f"{model_name}: {len(gone)} of {len(shards)} "
                                            f"shards missing (first: {gone[0].name})")
                           if gone else
                           Finding("ok", f"{model_name}: all {len(shards)} shards present"))
            size = sum(s.stat().st_size for s in shards) if shards else weights.stat().st_size
            if enrolled.size_bytes and size != enrolled.size_bytes:
                out.append(Finding("error", f"{model_name}: size changed — enrolled "
                                            f"{enrolled.size_bytes} bytes, on disk {size}"))
            else:
                out.append(Finding("ok", f"{model_name}: size unchanged ({size} bytes)"))
            if enrolled.identity:
                now = file_identity(weights)
                out.append(Finding("ok", f"{model_name}: identity unchanged ({now})")
                           if now == enrolled.identity else
                           Finding("error", f"{model_name}: identity changed — enrolled "
                                            f"{enrolled.identity}, on disk {now}: a "
                                            "different file sits at that path"))
        if enrolled.mmproj is not None:
            out.append(Finding("ok", f"{model_name}: projector present — {enrolled.mmproj}")
                       if enrolled.mmproj.is_file() else
                       Finding("error", f"{model_name}: projector missing — "
                                        f"{enrolled.mmproj} is not there any more"))

    server = data.get("server")
    if isinstance(server, dict) and server:
        flags = server_long_flags(llama_server)
        if flags is None:
            out.append(Finding("warn", f"{model_name}: server keys not checked — no "
                                       "llama-server binary found (--llama-server P "
                                       "names one)"))
        else:
            unknown = [k for k in server if k not in flags]
            for key in unknown:
                out.append(Finding("error", f"{model_name}: [server].{key} is not a "
                                            "flag this door accepts (checked against "
                                            "its own --help)"))
            if not unknown:
                out.append(Finding("ok", f"{model_name}: all {len(server)} [server] key(s) "
                                         "exist in the door's --help"))
    return out
