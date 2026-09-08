"""weights/scan.py — three readers over the filesystem, and nothing else.

Every layout Hearth understands is read from files and GGUF headers. No product
daemon is contacted, no product CLI is run, no product's private cache
directory (an index JSON, a header metadata cache) is opened — guard rail R1. The
consequence is the point: a scan gives the same answer with LM Studio and
Ollama quit, uninstalled, or renamed (guard rail R5).

  plain    a directory of `*.gguf`. Multi-part shards (`-00001-of-00005.gguf`)
           group into one candidate; a projector (`mmproj-*.gguf`, or any file
           whose header says architecture `clip`) is paired to the models
           beside it. This reader also serves an LM Studio tree, whose
           `<publisher>/<repo>/` shape is just directories.
  ollama   `manifests/**` OCI JSON → `blobs/sha256-<hex>`. A layer whose media
           type ends `.model` is the weights, `.projector` the mmproj. The blob
           is accepted only after its four magic bytes read `GGUF`, which is
           what skips safetensors imports (their layers end `.tensor`).
  hf       `models--<org>--<repo>/snapshots/<rev>/` — plain files that happen to
           be symlinks into `blobs/`; they are realpath'd like everything else,
           and shown under the display key `<org>/<repo>`.

Nothing here writes, moves, or deletes a weights file. Reading a header is the
expensive step (a 150k-token vocabulary costs seconds), so results are cached
by file identity under the data folder; the cache is a convenience and a scan
is correct without it.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from hearth.config import config_loader as cl

from .header import HeaderFacts, identity, is_gguf, read_header
from .roots import Root, resolve_roots

#: A multi-part GGUF: `…-00001-of-00005.gguf`.
SHARD_RE = re.compile(r"^(?P<base>.+)-(?P<index>\d{5})-of-(?P<total>\d{5})\.gguf$")
#: Ollama blob digests, as the manifest writes them.
_DIGEST_RE = re.compile(r"^sha256[:-]([0-9a-f]{8,64})$")
#: `models--<org>--<repo>` — the Hugging Face hub cache's repo directory.
_HF_DIR_RE = re.compile(r"^models--(?P<org>.+?)--(?P<repo>.+)$")

#: Directory names never descended into (on top of the blanket "starts with a
#: dot" rule). The first is a product's private header/index cache: reading it
#: instead of the file is exactly the coupling guard rail R1 forbids, so it is
#: named here, in the skip list, and nowhere else in this package.
HIDDEN_SKIP = (".internal", ".cache")

#: Header facts, keyed by file identity. A convenience, never a source of truth.
HEADER_CACHE_PATH = cl.DATA_DIR / ".cache" / "weights-headers.json"


# ── the fence: a root is a declaration, a symlink out of it is not ──────────
#
# A symlink inside a root that resolves OUTSIDE every declared root is reported
# and never followed. Two reasons. The root is the user's statement of what
# Hearth may read, and a link is not that statement. And a link can lead onto
# an external or network volume the daemon is not allowed to touch — on macOS
# that open() does not fail, it BLOCKS, waiting on a consent prompt no daemon
# will ever see (found live 2026-09-07: the launch page's scan hung in
# opendir on a link into an exFAT volume the terminal could read and the
# daemon could not). Readlink alone is safe; stat through the link is not, so
# the fence is checked before anything touches the target.

def _inside(target: Path, boundaries: list[Path] | None) -> bool:
    if boundaries is None:
        return True
    return any(target == b or b in target.parents for b in boundaries)


def _elsewhere(link: Path, target: Path, root: Root) -> "Candidate":
    try:
        rel = str(link.relative_to(root.path))
    except ValueError:
        rel = link.name
    return Candidate(path=link, display_key=rel, root_name=root.name,
                     layout="link", kind="elsewhere", size_bytes=0,
                     header_error=f"points outside your roots → {target} — "
                                  "add that folder as a root if Hearth may read it")


def _fence(entry: Path, root: Root, boundaries: list[Path] | None,
           out: list) -> bool:
    """True when `entry` is a symlink leaving the roots (recorded, not followed)."""
    if boundaries is None or not entry.is_symlink():
        return False
    target = Path(os.path.realpath(entry))
    if _inside(target, boundaries):
        return False
    out.append(_elsewhere(entry, target, root))
    return True


@dataclass
class Candidate:
    """One set of weights found on disk. A reference, never a copy."""
    path: Path                       # resolved real path (R2)
    display_key: str
    root_name: str
    layout: str                      # plain | ollama | hf
    kind: str                        # model | mmproj
    size_bytes: int
    shards: list[Path] = field(default_factory=list)
    header: HeaderFacts | None = None
    header_error: str | None = None
    identity: str = ""
    duplicates: list[Path] = field(default_factory=list)
    mmproj_candidates: list["Candidate"] = field(default_factory=list)

    @property
    def architecture(self) -> str | None:
        return self.header.architecture if self.header else None


# ── the header cache (identity → facts) ──────────────────────────────────────

_MEMO: dict[str, HeaderFacts] = {}
_DISK: dict[str, dict] | None = None
_DIRTY = False


def _disk_cache() -> dict[str, dict]:
    global _DISK
    if _DISK is None:
        try:
            _DISK = json.loads(HEADER_CACHE_PATH.read_text(encoding="utf-8"))
            if not isinstance(_DISK, dict):
                _DISK = {}
        except (OSError, ValueError):
            _DISK = {}
    return _DISK


def save_header_cache() -> None:
    """Persist what this scan learned. Failure is silent by design: a cache
    that cannot be written must never fail a scan."""
    global _DIRTY
    if not _DIRTY:
        return
    try:
        HEADER_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        HEADER_CACHE_PATH.write_text(json.dumps(_disk_cache(), indent=1) + "\n",
                                     encoding="utf-8")
        _DIRTY = False
    except OSError:
        pass


def header_for(path: Path, key: str, use_cache: bool = True) -> tuple[HeaderFacts | None, str | None]:
    """(facts, error) for one file, memoized on its identity."""
    global _DIRTY
    if use_cache:
        if key in _MEMO:
            return _MEMO[key], None
        cached = _disk_cache().get(key)
        if isinstance(cached, dict):
            try:
                facts = HeaderFacts(**cached)
            except TypeError:
                facts = None
            if facts is not None:
                _MEMO[key] = facts
                return facts, None
    try:
        facts = read_header(path)
    except Exception as exc:  # noqa: BLE001 — an unreadable header is a reported state
        return None, f"{type(exc).__name__}: {exc}"
    _MEMO[key] = facts
    if use_cache:
        _disk_cache()[key] = facts.as_dict()
        _DIRTY = True
    return facts, None


# ── the plain reader ─────────────────────────────────────────────────────────

def _sizes(paths: list[Path]) -> int:
    total = 0
    for p in paths:
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total


def _make(path: Path, display_key: str, root: Root, layout: str, kind: str,
          shards: list[Path], use_cache: bool) -> Candidate | None:
    try:
        real = Path(os.path.realpath(path))
        size = _sizes(shards or [real])
        ident = identity(real)
    except OSError:
        return None
    facts, err = header_for(real, ident, use_cache)
    return Candidate(path=real, display_key=display_key, root_name=root.name,
                     layout=layout, kind=kind, size_bytes=size,
                     shards=[Path(os.path.realpath(s)) for s in shards],
                     header=facts, header_error=err, identity=ident)


def _looks_like_mmproj(name: str, facts: HeaderFacts | None) -> bool:
    return "mmproj" in name.lower() or (facts is not None and facts.architecture == "clip")


def scan_dir(directory: Path, root: Root, layout: str = "plain",
             key_prefix: str | None = None, use_cache: bool = True,
             boundaries: list[Path] | None = None) -> list[Candidate]:
    """Every GGUF in ONE directory: shards grouped, projectors paired. A
    symlinked file that leaves the roots is reported, never opened."""
    fenced: list[Candidate] = []
    try:
        entries = []
        for p in sorted(directory.iterdir()):
            if not p.name.endswith(".gguf"):
                continue
            if _fence(p, root, boundaries, fenced):
                continue
            if p.is_file():
                entries.append(p)
    except OSError:
        return fenced

    groups: dict[str, list[Path]] = {}
    for path in entries:
        match = SHARD_RE.match(path.name)
        base = match.group("base") if match else path.name[: -len(".gguf")]
        groups.setdefault(base, []).append(path)

    out: list[Candidate] = []
    for base, paths in sorted(groups.items()):
        paths.sort()
        shards = paths if len(paths) > 1 or SHARD_RE.match(paths[0].name) else []
        if key_prefix is not None:
            key = f"{key_prefix}/{base}"
        else:
            try:
                rel = directory.relative_to(root.path)
                key = f"{rel}/{base}" if str(rel) != "." else base
            except ValueError:
                key = base
        cand = _make(paths[0], key, root, layout, "model", shards, use_cache)
        if cand is None:
            continue
        if _looks_like_mmproj(paths[0].name, cand.header):
            cand.kind = "mmproj"
        out.append(cand)

    projectors = [c for c in out if c.kind == "mmproj"]
    for cand in out:
        if cand.kind == "model":
            cand.mmproj_candidates = list(projectors)
    return out + fenced


# ── the Ollama reader ────────────────────────────────────────────────────────

def scan_ollama(store: Path, root: Root | None = None,
                use_cache: bool = True) -> list[Candidate]:
    """manifests/** → blobs/. Manifest JSON only; the daemon is never asked."""
    root = root or Root("ollama", store, "ollama")
    manifests = store / "manifests"
    blobs = store / "blobs"
    out: list[Candidate] = []
    if not manifests.is_dir() or not blobs.is_dir():
        return out

    for path in sorted(p for p in manifests.rglob("*") if p.is_file()):
        if path.name.startswith("."):
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        layers = doc.get("layers")
        if not isinstance(layers, list):
            continue

        def blob_of(media_suffix: str) -> Path | None:
            for layer in layers:
                if not isinstance(layer, dict):
                    continue
                if not str(layer.get("mediaType", "")).endswith(media_suffix):
                    continue
                match = _DIGEST_RE.match(str(layer.get("digest", "")))
                if match is None:
                    continue
                blob = blobs / f"sha256-{match.group(1)}"
                if blob.is_file():
                    return blob
            return None

        model_blob = blob_of(".model")
        # A safetensors import has `.tensor` layers and no loadable GGUF; a
        # blob whose magic is not GGUF is likewise not ours to enroll.
        if model_blob is None or not is_gguf(model_blob):
            continue

        rel = path.relative_to(manifests).parts
        tag = rel[-1]
        name = rel[-2] if len(rel) >= 2 else "model"
        namespace = rel[-3] if len(rel) >= 3 else "library"
        key = f"{namespace}/{name}:{tag}"

        model = _make(model_blob, key, root, "ollama", "model", [], use_cache)
        if model is None:
            continue
        proj_blob = blob_of(".projector")
        if proj_blob is not None and is_gguf(proj_blob):
            proj = _make(proj_blob, f"{key} (mmproj)", root, "ollama", "mmproj", [],
                         use_cache)
            if proj is not None:
                model.mmproj_candidates = [proj]
                out.append(proj)
        out.append(model)
    return out


# ── the walk ─────────────────────────────────────────────────────────────────

def scan_root(root: Root, use_cache: bool = True,
              boundaries: list[Path] | None = None) -> list[Candidate]:
    """Everything under one root, each directory read by the layout it shows.

    `boundaries` are the real paths of every declared root; a symlink that
    resolves outside all of them is reported as kind "elsewhere" and not
    walked. Alone, a root fences on itself.
    """
    if boundaries is None:
        boundaries = [Path(os.path.realpath(root.path))]
    out: list[Candidate] = []
    for dirpath, dirnames, _files in os.walk(root.path, followlinks=True):
        here = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames
                             if not d.startswith(".") and d not in HIDDEN_SKIP
                             and not _fence(here / d, root, boundaries, out))

        if "manifests" in dirnames and "blobs" in dirnames:
            out += scan_ollama(here, root, use_cache)
            dirnames[:] = []
            continue

        hf = _HF_DIR_RE.match(here.name)
        if hf is not None and "snapshots" in dirnames:
            key = f"{hf.group('org')}/{hf.group('repo')}"
            for rev in sorted(p for p in (here / "snapshots").iterdir() if p.is_dir()):
                for sub_dir, sub_dirs, _f in os.walk(rev, followlinks=True):
                    sub_dirs[:] = sorted(d for d in sub_dirs
                                         if not d.startswith(".") and d not in HIDDEN_SKIP)
                    out += scan_dir(Path(sub_dir), root, "hf", key, use_cache,
                                    boundaries)
            dirnames[:] = []
            continue

        out += scan_dir(here, root, "plain", None, use_cache, boundaries)
    return out


def scan_all(roots: list[Root] | None = None, use_cache: bool = True) -> list[Candidate]:
    """Every candidate under every resolved root, deduped by real path, with
    `duplicates` filled: the same identity found somewhere else."""
    roots = resolve_roots() if roots is None else roots
    boundaries = [Path(os.path.realpath(r.path)) for r in roots]
    found: list[Candidate] = []
    seen: set[Path] = set()
    for root in roots:
        for cand in scan_root(root, use_cache, boundaries):
            if cand.path in seen:
                continue
            seen.add(cand.path)
            found.append(cand)

    by_identity: dict[str, list[Candidate]] = {}
    for cand in found:
        if cand.identity:            # "elsewhere" rows were never read
            by_identity.setdefault(cand.identity, []).append(cand)
    for group in by_identity.values():
        if len(group) < 2:
            continue
        for cand in group:
            cand.duplicates = [o.path for o in group if o.path != cand.path]
    save_header_cache()
    return found
