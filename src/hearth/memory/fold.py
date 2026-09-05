"""memory/fold.py — fold a per-turn memory block into the newest user message.

Import-light on purpose: the serve facade and the voice prefetch processor
both need this and nothing else from the seam at import time.
"""

from __future__ import annotations


def with_turn_block(messages, block: str) -> list:
    """A NEW message list with `block` folded into the newest user message
    (string content or text parts); the input list and its dicts are never
    mutated. Empty block or no user message ⇒ the same list object back."""
    if not block:
        return messages
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, str):
            folded = dict(m, content=f"{content}\n\n{block}")
        elif isinstance(content, list):
            folded = dict(m, content=[*content, {"type": "text", "text": block}])
        else:
            return messages
        out = list(messages)
        out[i] = folded
        return out
    return messages


