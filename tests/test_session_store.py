"""test_session_store.py — headless proof of Tier 1 session continuity.

Runs WITHOUT mic / LM Studio (the voice loop can't be exercised here). It proves
the load-bearing invariants on the REAL artifacts:
  1. context.messages JSON round-trip through a real LLMContext (the #1 risk)
  2. system prompt is never persisted (no duplication on reload)
  3. saved-by-default on graceful stop; recall-only carve-out deletes; held sticky;
     hold-request promotes/names
  4. picker/guard metadata never carries content; sweep spares saved + held
  5. atomicity (no .tmp left) + private perms (dir 0700 / file 0600)
  6. malformed files never crash startup (fall back / skip)

Run:  .venv/bin/python test_session_store.py
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from pathlib import Path

from hearth.session import session_store as ss
from pipecat.processors.aggregators.llm_context import LLMContext, LLMSpecificMessage

_PASS = 0
_FAIL = 0


def check(cond, label):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  PASS  {label}")
    else:
        _FAIL += 1
        print(f"  FAIL  {label}")


def _store(tmp, sid="session-test", held=False, name=None, memory_mode="full"):
    return ss.SessionStore(
        session_id=sid, model="qwen-test", voice="default",
        prompt_sha256="deadbeef", sessions_dir=Path(tmp), held=held, name=name,
        memory_mode=memory_mode,
    )


def test_round_trip(tmp):
    print("\n[1] messages JSON round-trip through a real LLMContext (the #1 risk)")
    # Build a context exactly as the aggregators do (role/content dicts).
    ctx = LLMContext()
    ctx.add_message({"role": "user", "content": "remember the passphrase is copper-lantern-47"})
    ctx.add_message({"role": "assistant", "content": "Got it — copper-lantern-47. Locked in."})
    ctx.add_message({"role": "user", "content": "what was it again?"})
    ctx.add_message({"role": "assistant", "content": "copper-lantern-47."})
    original = list(ctx.messages)

    st = _store(tmp)
    st.snapshot(ctx.messages)
    data = ss.load(st.path)
    # Reload into a FRESH context — the resume path.
    ctx2 = LLMContext(messages=data["messages"])
    check(ctx2.messages == original, "reloaded context.messages equals the original")
    check(json.dumps(ctx2.messages) == json.dumps(original), "byte-identical JSON both directions")

    # The risk: a provider-specific (tool) message is NOT JSON-plain. Our serializer
    # must skip it rather than crash (none occur in this tool-free pipeline).
    ctx.add_message(LLMSpecificMessage(llm="openai", message={"role": "tool", "content": "x"}))
    st.snapshot(ctx.messages)  # must not raise
    data2 = ss.load(st.path)
    check(all(isinstance(m, dict) for m in data2["messages"]), "LLMSpecificMessage skipped, not crashed")
    check(len(data2["messages"]) == 4, "only the 4 plain messages persisted")


def test_system_excluded(tmp):
    print("\n[2] system prompt is never persisted (no duplication on reload)")
    ctx = LLMContext()
    ctx.add_message({"role": "system", "content": "SECRET PERSONA — should never be written"})
    ctx.add_message({"role": "user", "content": "hi"})
    st = _store(tmp, sid="session-sys")
    st.snapshot(ctx.messages)
    data = ss.load(st.path)
    roles = [m["role"] for m in data["messages"]]
    check("system" not in roles, "no system role in the persisted file")
    check(roles == ["user"], "only the user message persisted")


def test_saved_default(tmp):
    print("\n[3a] saved-by-default: graceful stop KEEPS the file; empty sitting saves nothing")
    ctx = LLMContext()
    ctx.add_message({"role": "user", "content": "hi"})
    st = _store(tmp, sid="session-saved")
    st.snapshot(ctx.messages)
    check(st.path.exists(), "file exists after a turn")
    status = ss.finalize(st, ctx.messages)
    check(st.path.exists(), "file SURVIVES graceful finalize (saved by default)")
    check("saved" in status, f"status reports saved ({status!r})")
    # zero-turn sitting: no file was ever snapshotted → finalize creates nothing
    empty = _store(tmp, sid="session-empty")
    status = ss.finalize(empty, [])
    check(not empty.path.exists(), "empty sitting leaves no file behind")
    check("nothing to save" in status, f"status reports empty ({status!r})")


def test_recall_only_carveout(tmp):
    print("\n[3a'] recall-only carve-out: transcript truly deleted on graceful stop; held exempt")
    ctx = LLMContext()
    ctx.add_message({"role": "user", "content": "private"})
    st = _store(tmp, sid="session-ro", memory_mode="recall-only")
    st.snapshot(ctx.messages)
    check(ss.load(st.path).get("memory_mode") == "recall-only", "leftover carries its stamp")
    status = ss.finalize(st, ctx.messages)
    check(not st.path.exists(), "recall-only file GONE after graceful finalize")
    check("deleted" in status, f"status reports deletion ({status!r})")
    # explicit keep wins over the carve-out
    kept = _store(tmp, sid="session-ro-held", held=True, memory_mode="recall-only")
    kept.snapshot(ctx.messages)
    status = ss.finalize(kept, ctx.messages)
    check(kept.path.exists(), "held recall-only file SURVIVES (deliberate keep wins)")
    check("kept" in status, f"status reports kept ({status!r})")


def test_held_exempt(tmp):
    print("\n[3b] held session is exempt from the ephemeral delete (sticky)")
    ctx = LLMContext()
    ctx.add_message({"role": "user", "content": "keep me"})
    st = _store(tmp, sid="session-held", held=True)
    st.snapshot(ctx.messages)
    status = ss.finalize(st, ctx.messages)
    check(st.path.exists(), "held file SURVIVES graceful finalize")
    check("kept" in status, f"status reports kept ({status!r})")


def test_hold_request_promotes(tmp):
    print("\n[3c] stop.sh --hold marker promotes an ephemeral session to held + names it")
    ctx = LLMContext()
    ctx.add_message({"role": "user", "content": "promote me"})
    st = _store(tmp, sid="session-promote")
    st.snapshot(ctx.messages)
    ss.write_hold_request("work-chat", sessions_dir=Path(tmp))
    status = ss.finalize(st, ctx.messages)
    old = Path(tmp) / "session-promote.json"
    new = Path(tmp) / "work-chat.json"
    check(not old.exists(), "original timestamp file renamed away")
    check(new.exists(), "renamed to work-chat.json")
    data = ss.load(new)
    check(data.get("held") is True and data.get("name") == "work-chat", "held:true + name persisted")
    check(not ss.marker_path(Path(tmp)).exists(), "hold-request marker consumed")
    check("held" in status, f"status reports held ({status!r})")


def test_picker_and_resolve(tmp):
    print("\n[4] picker/guard metadata is content-free; resolve by name works")
    d = Path(tmp) / "pick"
    ss.ensure_dir(d)
    _store(d, sid="session-a").snapshot([{"role": "user", "content": "SENSITIVE-A"}])
    st_b = _store(d, sid="convo-b", held=True, name="convo-b")
    st_b.snapshot([{"role": "user", "content": "SENSITIVE-B"}, {"role": "assistant", "content": "x"}])

    metas = ss.list_sessions(d)
    check(len(metas) == 2, "both sessions listed")
    # Metadata objects must not carry any message content.
    blob = json.dumps([m.__dict__ for m in metas], default=str)
    check("SENSITIVE" not in blob, "picker metadata contains NO conversation content")
    check(all(m.turns >= 1 for m in metas), "turn counts present in metadata")
    p = ss.resolve_resume_arg("convo-b", d)
    check(p is not None and p.name == "convo-b.json", "resolve by name → correct file")


def test_guard_and_discard(tmp):
    print("\n[5] sweep classes: only recall-only leftovers are ephemeral; saved + held spared")
    d = Path(tmp) / "guard"
    ss.ensure_dir(d)
    _store(d, sid="session-saved1").snapshot([{"role": "user", "content": "s1"}])
    _store(d, sid="session-ro1", memory_mode="recall-only").snapshot(
        [{"role": "user", "content": "r1"}])
    _store(d, sid="session-ro-kept", held=True,
           memory_mode="recall-only").snapshot([{"role": "user", "content": "r2"}])
    _store(d, sid="kept", held=True, name="kept").snapshot([{"role": "user", "content": "k"}])

    check(len(ss.ephemeral_orphans(d)) == 1, "only the recall-only leftover is ephemeral")
    check(len(ss.held_sessions(d)) == 2, "2 held sessions detected")
    removed = ss.discard_ephemeral(d)
    check(removed == ["session-ro1"], "sweep removed exactly the recall-only leftover")
    check((d / "session-saved1.json").exists(), "plain saved session untouched by the sweep")
    check((d / "session-ro-kept.json").exists(), "held recall-only file untouched by the sweep")
    check((d / "kept.json").exists(), "held file untouched by the sweep")
    check(len(ss.ephemeral_orphans(d)) == 0, "no leftovers remain")
    # name-it-now: hold with no bot promotes the newest not-yet-held session
    sid = ss.hold_latest_orphan("named-later", d)
    check(sid == "named-later", f"hold_latest_orphan named the saved session ({sid!r})")
    check(ss.load(d / "named-later.json").get("held") is True, "promoted file marked held")
    # explicit discard-held verb removes a held one (true delete)
    ss.discard_held("kept", d)
    check(not (d / "kept.json").exists(), "discard-held removed the held file")


def test_atomic_and_perms(tmp):
    print("\n[6] atomicity + private perms (no .tmp left; dir 0700 / file 0600)")
    d = Path(tmp) / "perms"
    st = _store(d, sid="session-perms")
    st.snapshot([{"role": "user", "content": "hi"}])
    tmps = list(d.glob("*.tmp"))
    check(not tmps, "no .tmp file left after snapshot")
    dmode = stat.S_IMODE(os.stat(d).st_mode)
    fmode = stat.S_IMODE(os.stat(st.path).st_mode)
    check(dmode == 0o700, f"sessions dir is 0700 (got {oct(dmode)})")
    check(fmode == 0o600, f"session file is 0600 (got {oct(fmode)})")


def test_malformed(tmp):
    print("\n[7] malformed / empty files never crash startup")
    d = Path(tmp) / "bad"
    ss.ensure_dir(d)
    (d / "empty.json").write_text("")
    (d / "garbage.json").write_text("{not json")
    (d / "wrong-shape.json").write_text('{"schema":1}')  # no messages list
    good = _store(d, sid="good")
    good.snapshot([{"role": "user", "content": "ok"}])
    metas = ss.list_sessions(d)  # must skip the 3 bad ones, keep the good one
    check([m.session_id for m in metas] == ["good"], "list_sessions skips all malformed files")
    raised = False
    try:
        ss.load(d / "wrong-shape.json")
    except ValueError:
        raised = True
    check(raised, "load() raises ValueError on malformed shape (caller falls back to fresh)")


def test_memory_mode_stamp(tmp):
    print("\n[7] memory-mode stamp: non-default persisted, resume inherits, flag wins")
    d = Path(tmp) / "modes"
    st = ss.SessionStore(session_id="s-ro", model="m", voice="v",
                         prompt_sha256="d", sessions_dir=d, memory_mode="recall-only")
    st.snapshot([{"role": "user", "content": "hi"}])
    data = ss.load(st.path)
    check(data.get("memory_mode") == "recall-only", "non-default mode persisted in the snapshot")

    full = ss.SessionStore(session_id="s-full", model="m", voice="v",
                           prompt_sha256="d", sessions_dir=d)
    full.snapshot([{"role": "user", "content": "hi"}])
    check("memory_mode" not in ss.load(full.path),
          "full sitting writes no stamp — its files stay byte-stable")

    resumed = ss.SessionStore(session_id="s-r2", model="m", voice="v",
                              prompt_sha256="d", sessions_dir=d,
                              memory_mode=str(data.get("memory_mode") or "full"))
    check(ss.inherit_memory_mode(None, resumed) == "recall-only",
          "flag absent — the resumed session's stamp is inherited")
    check(ss.inherit_memory_mode("full", resumed) == "full"
          and resumed.memory_mode == "full",
          "explicit flag wins and re-stamps the store")
    fresh = ss.SessionStore(session_id="s-f", model="m", voice="v",
                            prompt_sha256="d", sessions_dir=d)
    check(ss.inherit_memory_mode(None, fresh) == "full",
          "fresh sitting defaults to full")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        test_round_trip(tmp)
        test_system_excluded(tmp)
        test_saved_default(tmp)
        test_recall_only_carveout(tmp)
        test_held_exempt(tmp)
        test_hold_request_promotes(tmp)
        test_picker_and_resolve(tmp)
        test_guard_and_discard(tmp)
        test_atomic_and_perms(tmp)
        test_malformed(tmp)
        test_memory_mode_stamp(tmp)
    print(f"\n{'='*52}\n  RESULT: {_PASS} passed, {_FAIL} failed\n{'='*52}")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
