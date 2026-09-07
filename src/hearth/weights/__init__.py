"""hearth.weights — enrollment, not storage.

Hearth owns *which weights, where* — and owns it as a set of references, never
as a library. A ROOT is a directory it is willing to look in (yours, or one of
three built-in pointers at the folders LM Studio, Ollama and the Hugging Face
hub cache keep files in); ENROLLED WEIGHTS are a resolved real path written
into `config/models/<name>/model.toml` as a `[weights]` table, beside the
`[server]` flags the door will be given. Hearth never downloads, moves, copies
or deletes a weights file: un-enrolling removes the reference and nothing else,
and the file stays exactly where its owner put it. Five guard rails hold that
line, and are enforced by tests/test_weights_guardrails.py: **R1** the readers
touch files and GGUF headers only — no product daemon, CLI or private cache is
ever consulted; **R2** enrollment stores the resolved real path, so a symlink
through a product's dot-directory never becomes the reference; **R3** weights
that have gone missing are a reported state with a plain message, never an
exception and never a fall-back to some product's copy; **R4** nothing in the
live conversation loop imports this package — scanning is an enroll-time act;
**R5** a scan gives the same answer with every one of those products quit,
uninstalled, or renamed, because none of them was ever asked.

    python -m hearth.weights roots | scan | list | enroll | unenroll | check
"""

from __future__ import annotations

from .header import (
    GGUF_MAGIC,
    IDENTITY_PREFIX_BYTES,
    HeaderFacts,
    identity,
    is_gguf,
    read_header,
)
from .roots import (
    FALLBACK_LLAMA_SERVER,
    PRODUCT_POINTERS,
    ROLE_SUBDIRS,
    Root,
    WEIGHTS_TOML,
    WeightsConfig,
    landing_dir,
    llama_server_path,
    load_weights_config,
    resolve_roots,
    role_dirs,
)
from .scan import (
    HEADER_CACHE_PATH,
    HIDDEN_SKIP,
    SHARD_RE,
    Candidate,
    header_for,
    save_header_cache,
    scan_all,
    scan_dir,
    scan_ollama,
    scan_root,
)
from .fit import (
    MARGIN_BYTES,
    MIN_CTX,
    RESERVE_BYTES,
    Budget,
    Estimate,
    attention_layers,
    estimate,
    kv_bytes,
    machine_budget,
    parse_devices,
    verdict,
)
from .enroll import (
    COMMENT_MARK,
    KEY_ORDER,
    EnrolledWeights,
    Finding,
    WeightsError,
    check,
    data_model_toml,
    enroll,
    enrolled_models,
    load_enrolled,
    model_toml,
    remove_weights_table,
    server_long_flags,
    unenroll,
    write_weights_table,
)

__all__ = [
    "Budget", "Candidate", "EnrolledWeights", "Estimate", "Finding",
    "GGUF_MAGIC", "HeaderFacts", "Root", "WeightsConfig", "WeightsError",
    "attention_layers", "check", "enroll", "enrolled_models", "estimate",
    "identity", "is_gguf", "kv_bytes", "landing_dir", "llama_server_path",
    "load_enrolled", "load_weights_config", "machine_budget", "read_header",
    "remove_weights_table", "resolve_roots", "role_dirs", "scan_all",
    "scan_dir", "scan_ollama", "scan_root", "server_long_flags", "unenroll",
    "verdict", "write_weights_table",
]
