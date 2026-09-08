"""weights/roots.py — the directories Hearth is willing to look in.

A ROOT is a directory, nothing more. `config/weights.toml` names the operator's
own roots; three built-in POINTERS name the directories the popular
weights-serving products keep files in (LM Studio, Ollama, the Hugging Face hub
cache). A pointer is a path, never a product: no daemon, no CLI, no private
cache is ever consulted (guard rail R1). Deleting one of those products changes
nothing here except that a directory stops existing — which the scan reports as
absent, and enrolled weights under it report as missing.

Absent file → defaults. Malformed file → ConfigError naming it. The same
contract every installation-level Hearth config file follows.

    [weights]
    roots = ["/Users/<you>/models"]     # your own directories
    product_dirs = true               # also point at LM Studio / Ollama / HF
    landing = "/Users/<you>/models/hearth"   # Hearth's own folder for new weights
    llama_server = "/opt/homebrew/bin/llama-server"

    [weights.door]                    # the door's own facts, not any model's
    label = "com.hearth.llm"
    port = 8080

The `[weights.door]` table is the SECOND thing this file holds: the facts that
belong to the door itself rather than to any one model — where it listens, the
PATH to its access key, how it loads, where it logs. A model's `[server]` table
carries what that model needs; swap models and the door table does not move.
The renderer (`weights/render.py`) is its only consumer.
"""

from __future__ import annotations

import os
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from hearth.config.config_loader import CONFIG_DIR, ConfigError

#: config/weights.toml — installation-level, place scope, operator-owned.
WEIGHTS_TOML = CONFIG_DIR / "weights.toml"

#: The built-in pointers: (root name, directory). Each is included only when it
#: exists AND its real path is not already inside one of the operator's roots
#: (on this machine both product folders are symlinks INTO the user's own
#: models folder, so they are already covered and must not scan twice).
PRODUCT_POINTERS: tuple[tuple[str, str], ...] = (
    ("lmstudio", "~/.lmstudio/models"),
    ("ollama", "~/.ollama/models"),
    ("hf", "~/.cache/huggingface/hub"),
)

#: The landing folder's role subdivision. G1 scans `llm/` only — speech weights
#: are library-fetched today and get their own enrollment when those stages are.
ROLE_SUBDIRS: tuple[str, ...] = ("llm", "tts", "stt")

#: Where the door binary is looked for when weights.toml does not say.
FALLBACK_LLAMA_SERVER = "/opt/homebrew/bin/llama-server"

#: The launchd label the door runs under when the file does not say.
DEFAULT_LABEL = "com.hearth.llm"

#: The door's `--load-mode` vocabulary, verbatim from its own --help. It
#: replaces the deprecated `--mlock` / `--mmap` / `--direct-io` trio.
LOAD_MODES: tuple[str, ...] = ("auto", "none", "mmap", "mlock", "mmap+mlock", "dio")


@dataclass(frozen=True)
class Root:
    """One directory the scanner may walk.

    kind is provenance, not behaviour: "user" (yours), "landing" (Hearth's own
    folder, `llm/` role), or "lmstudio" / "ollama" / "hf" (a built-in pointer).
    The reader picked for a directory is decided by what is IN it, never by the
    root's kind.
    """
    name: str
    path: Path
    kind: str


@dataclass(frozen=True)
class DoorConfig:
    """`[weights.door]` — what the DOOR is, as against what a model is.

    Everything here would stay true if every model on the machine were
    replaced tomorrow: the launchd label, the address it listens on, the PATH
    to its access key, how many threads it may use, how it loads the file,
    where it writes its log. `api_key_file` is a path and stays a path — the
    renderer passes it to `--api-key-file` and never opens it.
    """
    label: str = DEFAULT_LABEL
    host: str = "127.0.0.1"
    port: int = 8080
    api_key_file: str | None = None
    threads: int | None = None
    load_mode: str | None = None
    log_file: str | None = None
    webui: bool = False
    args: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class WeightsConfig:
    roots: list[str] = field(default_factory=list)
    product_dirs: bool = True
    landing: str | None = None
    llama_server: str | None = None
    door: DoorConfig = field(default_factory=DoorConfig)
    source: Path | None = None   # the file it came from, or None for defaults


def _real(path: Path) -> Path:
    return Path(os.path.realpath(Path(path).expanduser()))


def load_weights_config(path: Path | None = None) -> WeightsConfig:
    """Read `[weights]` from config/weights.toml. Absent → defaults."""
    path = Path(path) if path is not None else WEIGHTS_TOML
    if not path.is_file():
        return WeightsConfig()
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"malformed TOML in {path}: {exc}") from None
    except OSError as exc:
        raise ConfigError(f"unreadable config file: {path} ({type(exc).__name__})") from None
    table = data.get("weights")
    if table is None:
        return WeightsConfig(source=path)
    if not isinstance(table, dict):
        raise ConfigError(f"{path}: [weights] must be a table")
    roots = table.get("roots", [])
    if not isinstance(roots, list) or not all(isinstance(r, str) for r in roots):
        raise ConfigError(f"{path}: [weights].roots must be a list of directory paths")
    product_dirs = table.get("product_dirs", True)
    if not isinstance(product_dirs, bool):
        raise ConfigError(f"{path}: [weights].product_dirs must be true or false")
    for key in ("landing", "llama_server"):
        if key in table and table[key] is not None and not isinstance(table[key], str):
            raise ConfigError(f"{path}: [weights].{key} must be a path string")
    return WeightsConfig(
        roots=list(roots),
        product_dirs=product_dirs,
        landing=table.get("landing"),
        llama_server=table.get("llama_server"),
        door=_door_config(table.get("door"), path),
        source=path,
    )


def _door_config(table, path: Path) -> DoorConfig:
    """`[weights.door]`, defaults applied. Absent → the defaults, unchanged."""
    if table is None:
        return DoorConfig()
    if not isinstance(table, dict):
        raise ConfigError(f"{path}: [weights.door] must be a table")
    for key in ("label", "host", "api_key_file", "load_mode", "log_file"):
        if key in table and table[key] is not None and not isinstance(table[key], str):
            raise ConfigError(f"{path}: [weights.door].{key} must be a string")
    for key in ("port", "threads"):
        value = table.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise ConfigError(f"{path}: [weights.door].{key} must be a whole number")
    if "webui" in table and not isinstance(table["webui"], bool):
        raise ConfigError(f"{path}: [weights.door].webui must be true or false")
    args = table.get("args", [])
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ConfigError(f"{path}: [weights.door].args must be a list of strings")
    mode = table.get("load_mode")
    if mode is not None and mode not in LOAD_MODES:
        raise ConfigError(f"{path}: [weights.door].load_mode must be one of "
                          f"{', '.join(LOAD_MODES)} (the door's own --load-mode "
                          "vocabulary; --mlock and --no-direct-io are deprecated "
                          "in favour of it)")
    label = str(table.get("label") or DEFAULT_LABEL)
    if not label.strip() or "/" in label:
        raise ConfigError(f"{path}: [weights.door].label must be a launchd label, "
                          f"not {label!r}")
    return DoorConfig(
        label=label,
        host=str(table.get("host") or "127.0.0.1"),
        port=int(table["port"]) if table.get("port") is not None else 8080,
        api_key_file=table.get("api_key_file"),
        threads=table.get("threads"),
        load_mode=mode,
        log_file=table.get("log_file"),
        webui=bool(table.get("webui", False)),
        args=list(args),
    )


def landing_dir(cfg: WeightsConfig | None = None) -> Path | None:
    """Hearth's own folder for weights that arrive from here on.

    Declared, or `<first root>/hearth` when roots are given, else nothing —
    Hearth does not invent a directory on a machine that named none.
    """
    cfg = cfg or load_weights_config()
    if cfg.landing:
        return _real(Path(cfg.landing))
    if cfg.roots:
        return _real(Path(cfg.roots[0])) / "hearth"
    return None


def role_dirs(cfg: WeightsConfig | None = None) -> list[tuple[str, Path]]:
    """(role, directory) for the landing folder's three role subfolders."""
    base = landing_dir(cfg)
    return [] if base is None else [(role, base / role) for role in ROLE_SUBDIRS]


def llama_server_path(cfg: WeightsConfig | None = None) -> str | None:
    """The door binary: declared, else on PATH, else Homebrew's, else nothing."""
    cfg = cfg or load_weights_config()
    if cfg.llama_server:
        return str(Path(cfg.llama_server).expanduser())
    found = shutil.which("llama-server")
    if found:
        return found
    return FALLBACK_LLAMA_SERVER if Path(FALLBACK_LLAMA_SERVER).is_file() else None


def _inside(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def resolve_roots(cfg: WeightsConfig | None = None) -> list[Root]:
    """Every root that exists on this machine, deduped by real path.

    Order is provenance order: the operator's own roots, then Hearth's landing
    folder (its `llm/` role), then the product pointers. A pointer whose real
    path already sits inside one of the operator's roots is dropped — it is the
    same directory reached by another name.
    """
    cfg = cfg or load_weights_config()
    out: list[Root] = []
    seen: set[Path] = set()
    used_names: set[str] = set()

    def add(name: str, path: Path, kind: str) -> None:
        if not path.is_dir() or path in seen:
            return
        candidate = name or path.name or kind
        n, i = candidate, 2
        while n in used_names:
            n, i = f"{candidate}-{i}", i + 1
        used_names.add(n)
        seen.add(path)
        out.append(Root(n, path, kind))

    user_paths: list[Path] = []
    for raw in cfg.roots:
        real = _real(Path(raw))
        user_paths.append(real)
        add(real.name, real, "user")

    base = landing_dir(cfg)
    if base is not None:
        add("hearth", base / "llm", "landing")

    if cfg.product_dirs:
        for name, raw in PRODUCT_POINTERS:
            real = _real(Path(raw))
            if not real.is_dir():
                continue
            if any(_inside(real, u) for u in user_paths):
                continue
            add(name, real, name)
    return out
