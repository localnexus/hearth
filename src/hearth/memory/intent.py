"""intent.py — the intent slot: "next time, let's talk about X" survives the gap.

Signed design (2026-08-30, intent-primed boot recall). One targeted question
rides the extraction lane at session close — *did the user state what they want
to discuss next session?* — and the answer, when there IS one, lands in a
per-companion slot beside the records:
DATA/characters/<c>/memory/intent.json, 0600 in a 0700 tree.

Three properties this module exists to hold:

  * **Conservative capture.** A wrongly-inferred intent asserted at the next
    boot is a confident wrong memory — the exact failure the seam's provenance
    framing is built to avoid. So the prompt demands an EXPLICIT statement and
    the parser is hostile: anything that isn't a short, plausible topic (empty,
    "none", a sentence of hedging, an essay) yields no slot at all.
  * **Consume-once.** The slot is read at boot, injected once, then deleted —
    a plan that re-asserts itself for weeks is worse than no plan. An expiry
    backstop covers the long-gap case where "next time" lost its referent.
  * **Sidecar, never substrate.** Losing this file loses one hint, never a
    memory: it is deliberately NOT part of the record-replay contract
    (decider 7). Every failure here is logged and dropped.

The transport is stdlib urllib against a local Ollama — no new dependency, and
the extraction model is the one [memory.hindsight] already names.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

# Same package, same contract (atomic tmp → fsync → replace, 0600 in a 0700
# tree): reuse the record writer's helpers rather than restating them.
from .records import _atomic_write_json, _ensure_dir

SCHEMA = 1
KIND = "memory-intent"

_MAX_TOPIC_CHARS = 200      # a topic, not a paragraph — longer ⇒ the model rambled
_MAX_TRANSCRIPT_CHARS = 2000  # tail handed to the extraction model
_TIMEOUT_S = 30.0
_DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"

_PROMPT = """Read the end of this conversation transcript and answer ONE question:
did the user EXPLICITLY state what they want to talk about next time — in the
next session, the next conversation?

Answer with just that topic, a few words, in the user's own wording where you
can. If the user did not explicitly say what to pick up next time, answer with
the single word:
none

Rules: explicit statements only. Never infer a topic from enthusiasm, from what
happened to be discussed, or from a plan about anything other than the next
conversation. When in doubt, answer none. No explanation, no preamble.

TRANSCRIPT (tail):
{transcript}

ANSWER:"""


# ── the slot file ────────────────────────────────────────────────────────────

def intent_path(companion: str) -> Path:
    """DATA/characters/<companion>/memory/intent.json (not created until write)."""
    from hearth.config import config_loader  # lazy: keeps import cost off the CLI path

    return config_loader.companion_state_dir(companion, "memory") / "intent.json"


def write_slot(companion: str, text: str, source_session: str,
               path: Path | None = None) -> Path:
    """Persist the stated intent; returns its path. One slot, newest wins — a
    new statement replaces the old, which is the conversational semantics."""
    path = Path(path) if path is not None else intent_path(companion)
    _ensure_dir(path.parent)
    payload = {
        "schema": SCHEMA,
        "kind": KIND,
        "text": str(text),
        "stated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_session": str(source_session),
    }
    _atomic_write_json(path, payload)
    return path


def clear_slot(path: Path) -> None:
    """Delete the slot, contained — a slot we failed to remove would re-assert."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("[memory] intent slot could not be cleared ({})", type(exc).__name__)


def load_slot(companion: str, expiry_days: int = 14,
              path: Path | None = None) -> Optional[dict]:
    """The boot read: a usable slot, or None — never an exception.

    Malformed or stale ⇒ the file is DELETED and None returned: a slot we
    cannot trust must not survive to confuse the next boot either. ``text`` and
    ``stated_at`` come back for the injection line; the caller consumes the
    slot only once it has actually been used (see MemorySeam.augment).
    """
    path = Path(path) if path is not None else intent_path(companion)
    try:
        if not path.is_file():
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        text = str(data.get("text", "")).strip() if isinstance(data, dict) else ""
        if not isinstance(data, dict) or data.get("kind") != KIND or not text:
            logger.warning("[memory] malformed intent slot — discarded")
            clear_slot(path)
            return None
        if _is_stale(str(data.get("stated_at", "")), expiry_days):
            logger.info("[memory] intent slot expired (>{}d) — cleared, not used", expiry_days)
            clear_slot(path)
            return None
        return {"text": text,
                "stated_at": str(data.get("stated_at", "")),
                "source_session": str(data.get("source_session", "")),
                "path": path}
    except Exception as exc:  # noqa: BLE001 — a hint must never cost a boot
        logger.warning("[memory] intent slot read failed ({}) — ignored", type(exc).__name__)
        try:
            clear_slot(path)
        except Exception:  # noqa: BLE001
            pass
        return None


def _is_stale(stated_at: str, expiry_days: int) -> bool:
    """Expiry backstop. expiry_days <= 0 disables it (the date in the injected
    line then does all the staleness work). An unparseable stamp counts as
    stale — we cannot date the line honestly without one."""
    if expiry_days <= 0:
        return False
    try:
        stamp = datetime.fromisoformat(stated_at)
    except (TypeError, ValueError):
        return True
    now = datetime.now(stamp.tzinfo) if stamp.tzinfo else datetime.now()
    return (now - stamp).days > expiry_days


# ── capture (session close, extraction lane) ─────────────────────────────────

def render_tail(messages, max_chars: int = _MAX_TRANSCRIPT_CHARS) -> str:
    """Speaker-labelled tail of the conversation — the last few turns are where
    "next time, let's…" lives, and the cap bounds session-end latency."""
    lines: list[str] = []
    for m in messages or []:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
            continue
        content = " ".join(str(m.get("content", "")).split())
        if content:
            lines.append(f"{'User' if m['role'] == 'user' else 'Assistant'}: {content}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[-max_chars:]
        cut = text.find("\n")  # drop the partial first line after the cut
        if 0 <= cut < len(text) - 1:
            text = text[cut + 1:]
    return text


def _ollama_chat(url: str, model: str, prompt: str, timeout: float = _TIMEOUT_S) -> str:
    """POST /api/chat, non-streaming, temperature 0. Stdlib only — this lane
    must not add a dependency to the base install. Tests patch this seam."""
    body = json.dumps({
        "model": model,
        "stream": False,
        "options": {"temperature": 0},
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        url.rstrip("/") + "/api/chat", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — loopback, config-named
        payload = json.loads(resp.read().decode("utf-8"))
    return str((payload.get("message") or {}).get("content", ""))


def parse_answer(answer: str) -> Optional[str]:
    """The hostile parser: a short explicit topic, or None.

    Rejects the empty answer, "none" in any casing, and anything long enough to
    be the model explaining itself instead of naming a topic — the conservative
    posture is that a missed intent costs nothing while a wrong one is asserted
    at boot as a memory.
    """
    text = str(answer or "")
    if "</think>" in text:  # a reasoning model's preamble is not the answer
        text = text.rsplit("</think>", 1)[1]
    text = text.strip().strip("`").strip()
    text = text.splitlines()[0].strip() if text else ""
    text = text.strip('"“”\'').strip()
    if not text or text.lower().rstrip(".!") == "none":
        return None
    if len(text) > _MAX_TOPIC_CHARS:
        logger.warning("[memory] intent answer too long to be a topic — discarded")
        return None
    return text


def capture(companion: str, messages, session_id: str, cfg: dict,
            path: Path | None = None) -> Optional[str]:
    """Ask the extraction model the one question; write the slot if answered.

    Returns the captured topic, or None (no intent stated, or anything at all
    went wrong). Never raises: this runs at session close, behind the canonical
    record, and close must complete regardless.
    """
    provider = str(cfg.get("llm_provider") or "").strip().lower()
    model = str(cfg.get("llm_model") or "").strip()
    url = str(cfg.get("llm_url") or "").strip() or _DEFAULT_OLLAMA_URL
    if provider != "ollama":
        logger.warning("[memory] intent capture supports provider 'ollama' only "
                       "(got {!r}) — skipped", provider)
        return None
    if not model:
        logger.warning("[memory] intent capture needs llm_model "
                       "([memory.intent] or [memory.hindsight]) — skipped")
        return None
    transcript = render_tail(messages)
    if not transcript:
        return None
    try:
        answer = _ollama_chat(url, model, _PROMPT.format(transcript=transcript))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        logger.warning("[memory] intent capture LLM call failed ({}) — skipped",
                       type(exc).__name__)
        return None
    except Exception as exc:  # noqa: BLE001 — close must never fail on a hint
        logger.warning("[memory] intent capture failed ({}) — skipped", type(exc).__name__)
        return None
    topic = parse_answer(answer)
    if topic is None:
        return None
    try:
        write_slot(companion, topic, session_id, path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[memory] intent slot write failed ({}) — nothing kept",
                       type(exc).__name__)
        return None
    logger.info("[memory] intent captured for next session ({} chars)", len(topic))
    return topic
