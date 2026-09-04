"""settings_registry/validate.py — SchemaError, the shape checks the loader and
`config.check` share, and the JSON Schema bundle.

Sliced out of the single settings_registry.py it used to share; see the
package __init__ for the layout and the order the parts import in.
"""

from __future__ import annotations

import types
from typing import Union, get_args, get_origin

from pydantic import BaseModel, ValidationError

from .knobs import CANONICAL_TAGS
from .registry import REGISTRY

# ── validation ───────────────────────────────────────────────────────────────

class SchemaError(ValueError):
    """A type violation on a present key (loader mode). config_loader converts
    this to ConfigError so startup errors keep naming the offending file."""


# Constraint-class pydantic error types: warnings at load time, errors under
# `check`. Everything else non-missing is a type violation → error in both.
_WARN_TYPES = frozenset({
    "greater_than_equal", "less_than_equal", "greater_than", "less_than",
    "literal_error", "string_too_long", "string_pattern_mismatch", "multiple_of",
})


def _model_of(annotation) -> type[BaseModel] | None:
    """Unwrap Optional[Model] / Model → the model class, else None."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    if get_origin(annotation) in (types.UnionType, Union):
        for arg in get_args(annotation):
            if isinstance(arg, type) and issubclass(arg, BaseModel):
                return arg
    return None


def _dict_value_model_of(annotation) -> type[BaseModel] | None:
    """dict[str, Model] → Model, else None."""
    if get_origin(annotation) is dict:
        args = get_args(annotation)
        if len(args) == 2 and isinstance(args[1], type) and issubclass(args[1], BaseModel):
            return args[1]
    return None


def _unknown_keys(model_cls: type[BaseModel], data: dict, prefix: str = "") -> list[str]:
    """Recursive unknown-key report against the declared fields (keys only —
    never values). The coverage property: a key cannot exist without appearing."""
    out: list[str] = []
    fields = model_cls.model_fields
    for key, value in data.items():
        if key not in fields:
            out.append(f"unknown key '{prefix}{key}'")
            continue
        ann = fields[key].annotation
        sub = _model_of(ann)
        if sub is not None and isinstance(value, dict):
            out.extend(_unknown_keys(sub, value, f"{prefix}{key}."))
            continue
        val_model = _dict_value_model_of(ann)
        if val_model is not None and isinstance(value, dict):
            for name, item in value.items():
                if isinstance(item, dict):
                    out.extend(_unknown_keys(val_model, item, f"{prefix}{key}.{name}."))
    return out


def _validate(kind: str, data: dict, *, strict: bool) -> tuple[list[str], list[str]]:
    entry = REGISTRY[kind]
    warnings = _unknown_keys(entry.model, data)
    if kind == "tts-baseline":
        for tag in (data.get("tag_profiles") or {}):
            if tag not in CANONICAL_TAGS:
                warnings.append(f"[tag_profiles.{tag}] is not a canonical tag (runtime ignores it)")
    errors: list[str] = []
    try:
        entry.model.model_validate(data)
    except ValidationError as exc:
        for err in exc.errors():
            dotted = ".".join(str(p) for p in err["loc"])
            msg = f"{dotted}: {err['msg']}"  # pydantic msg carries no input value
            if err["type"] == "missing":
                if strict:
                    errors.append(f"missing required key '{dotted}'")
            elif err["type"] in _WARN_TYPES:
                (errors if strict else warnings).append(msg)
            else:
                errors.append(msg)
    return errors, warnings


def loader_check(kind: str, data: dict) -> list[str]:
    """Load-time posture: returns warnings (unknown keys, out-of-range values);
    raises SchemaError on a type violation on a present key."""
    errors, warnings = _validate(kind, data, strict=False)
    if errors:
        raise SchemaError("; ".join(errors))
    return warnings


def strict_check(kind: str, data: dict) -> tuple[list[str], list[str]]:
    """`check` posture: (errors, warnings) with required-ness and constraints binding."""
    return _validate(kind, data, strict=True)


# ── JSON Schema emission (the step-2 form contract) ──────────────────────────

def json_schema() -> dict[str, dict]:
    return {
        kind: {
            "title": e.title, "path": e.path, "role": e.role, "owner": e.owner,
            "layer": e.layer, "restart": e.restart, "note": e.note,
            "schema": e.model.model_json_schema(),
        }
        for kind, e in REGISTRY.items()
    }


