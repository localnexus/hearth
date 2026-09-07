"""weights/header.py — what a GGUF file says about itself, read cheaply.

Two readers, both safe on a 38 GB file because neither one reads tensor DATA:

  read_header(path) → HeaderFacts   the KV header + the tensor INFO table
                                    (gguf.GGUFReader memmaps; we sum the
                                    per-tensor byte counts it reports).
  identity(path)    → 16 hex chars  sha256 over the file size (8 bytes, LE)
                                    plus the first 1 MiB — the cheap duplicate
                                    key. Two files that agree here are, in
                                    practice, the same weights in two places
                                    (one machine held a 38 GB build twice:
                                    a plain file and an Ollama blob).

The header is the truth. No product's own index, cache, daemon or CLI is ever
consulted for a fact that lives in the file (guard rail R1).
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import asdict, dataclass
from pathlib import Path

#: The four bytes every GGUF file starts with. An Ollama blob is accepted only
#: after this is verified — a safetensors import is not loadable by the door.
GGUF_MAGIC = b"GGUF"

#: How much of the file the duplicate key hashes. One MiB covers the whole KV
#: header of every model seen so far and costs one read.
IDENTITY_PREFIX_BYTES = 1024 * 1024


@dataclass(frozen=True)
class HeaderFacts:
    """The header fields Hearth's fit arithmetic and display need.

    Architecture-specific keys are read as `<arch>.<suffix>` (the GGUF
    convention): on the incumbent that is `qwen35moe.block_count`,
    `qwen35moe.attention.head_count_kv`, `qwen35moe.attention.key_length`,
    `qwen35moe.full_attention_interval`, `qwen35moe.nextn_predict_layers`.
    Anything a file does not declare stays None — absent is a fact, not an
    error. `nextn_predict_layers` is the one exception: absent means zero
    speculative layers, which is what the arithmetic wants.
    """
    architecture: str | None = None
    name: str | None = None
    block_count: int | None = None
    context_length: int | None = None
    head_count: int | None = None
    head_count_kv: int | None = None
    key_length: int | None = None
    value_length: int | None = None
    full_attention_interval: int | None = None
    expert_count: int | None = None
    nextn_predict_layers: int = 0
    file_type: int | None = None
    tensor_bytes: int = 0

    def as_dict(self) -> dict:
        return asdict(self)

    def declared(self) -> dict:
        """Only the fields this file actually declared — what enrollment writes."""
        return {k: v for k, v in asdict(self).items() if v is not None}


def _scalar(value):
    """A KV that arrives as a one-element array is that element."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _int(value) -> int | None:
    value = _scalar(value)
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def is_gguf(path: Path) -> bool:
    """True if the first four bytes are the GGUF magic. Never reads further."""
    try:
        with open(path, "rb") as fh:
            return fh.read(4) == GGUF_MAGIC
    except OSError:
        return False


def read_header(path: Path) -> HeaderFacts:
    """Header + tensor-info facts for one GGUF file. Never reads tensor data."""
    from gguf import GGUFReader  # imported here: a scan of a plain root that
    # finds nothing need not pay for numpy.

    reader = GGUFReader(Path(path), "r")

    def kv(key: str):
        field = reader.fields.get(key)
        return None if field is None else field.contents()

    arch = _scalar(kv("general.architecture"))
    arch = str(arch) if arch is not None else None
    prefix = arch or ""

    def arch_kv(suffix: str):
        return kv(f"{prefix}.{suffix}") if prefix else None

    name = _scalar(kv("general.name"))
    return HeaderFacts(
        architecture=arch,
        name=str(name) if name is not None else None,
        block_count=_int(arch_kv("block_count")),
        context_length=_int(arch_kv("context_length")),
        head_count=_int(arch_kv("attention.head_count")),
        head_count_kv=_int(arch_kv("attention.head_count_kv")),
        key_length=_int(arch_kv("attention.key_length")),
        value_length=_int(arch_kv("attention.value_length")),
        full_attention_interval=_int(arch_kv("full_attention_interval")),
        expert_count=_int(arch_kv("expert_count")),
        nextn_predict_layers=_int(arch_kv("nextn_predict_layers")) or 0,
        file_type=_int(kv("general.file_type")),
        tensor_bytes=int(sum(int(t.n_bytes) for t in reader.tensors)),
    )


def identity(path: Path) -> str:
    """The cheap duplicate key: sha256(size as 8-byte LE ‖ first 1 MiB)[:16]."""
    path = Path(path)
    digest = hashlib.sha256()
    digest.update(struct.pack("<Q", path.stat().st_size))
    with open(path, "rb") as fh:
        digest.update(fh.read(IDENTITY_PREFIX_BYTES))
    return digest.hexdigest()[:16]
