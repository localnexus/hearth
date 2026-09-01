"""pipeline/switcher.py — the LIVE companion switch (ADR 007 stroke 3).

The turn-boundary escalation of ``/admin/switch``: the SAME selection bundle
that stroke 2 delivered as a supervised restart applies IN-PROCESS at the next
turn boundary — persona + model-template re-compose, LLM model FIELD swap
(resident models only, M4c), voice re-clone, and the **session-swap
primitive**: finalize the current companion's session (memory record + store
delete-decision, exactly the graceful-stop semantics) and seed the new
companion's context (fresh or resumed) with its own recall. The supervisor's
router decides which path a switch takes; this module is the live half.

Design constraints honored:
  * NO pipecat imports — ``ConfigReloadProcessor`` (config_reload.py) is the
    only frame-touching consumer: it calls ``apply_pending()`` on the turn's
    LLMContextFrame and turns the returned plain delta dict into the
    LLMUpdateSettingsFrame itself. This keeps the module importable (and unit-
    testable) in the base install without the [mac] extra.
  * One intent slot, last-wins: ``prepare()`` arms a fully-validated, fully-
    PREPARED bundle (loads + memory attach + recall run eagerly, off the event
    loop, at POST time) so the frame-path apply is cheap; a second prepare
    supersedes the first (its prepared seam is closed).
  * Heavy old-side work (memory record + consolidate + store finalize) runs in
    a background thread AFTER the swap — the new companion answers while the
    old session records. Failures degrade and log; they never break the loop.
  * File discipline (ADR 007 §2): ``active.toml`` stays the durable selection
    record. On apply, the selection is written iff the file does not already
    carry it (the daemon path pre-writes; a direct panel arm converges here).
  * POL-GL-039: every status/response carries names, states, and warnings
    only — never prompt text, message content, or tokens.

Shutdown contract: bot.py's finally-path reads ``current_store`` /
``current_seam`` from here (a live switch must be honored at stop), awaits
``drain()`` for an in-flight old-side finalize, and calls ``close_pending()``
for an armed-but-never-applied intent's seam.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

import aiohttp
from loguru import logger

from hearth.config import config_loader
from hearth.session import session_store
from hearth.supervisor import switch as switch_mod

# Provider aliases for the residency probe — mirrors
# engine_probe_llamaserver._LLAMASERVER_ALIASES (parity-tested).
_LLAMA_ALIASES = frozenset({"llama-server", "llamaserver", "llama.cpp", "llamacpp"})


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _default_seam_factory(character: str, persona: str):
    """The real memory seam (lazy import — keeps this module import-light)."""
    from hearth import memory as hearth_memory

    return hearth_memory.maybe_attach(character, persona=persona)


async def fetch_resident_ids(provider: str, base_url: str, token: str):
    """Model ids the LLM server holds RIGHT NOW (M4c: the live model-field
    swap offers resident models only).

    llama-server → /v1/models (the one loaded model, by alias/filename).
    LM Studio    → /api/v0/models filtered to state == "loaded" (its /v1/models
                   lists every DOWNLOADED model — that is not residency).
    → list of ids, or None on ANY failure (the caller then refuses the live
    model swap honestly and the router falls back to a restart). Never raises;
    never logs the token.
    """
    key = (provider or "").strip().lower()
    host = base_url.rstrip("/")
    try:
        timeout = aiohttp.ClientTimeout(total=3)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with aiohttp.ClientSession(timeout=timeout) as session:
            if key in _LLAMA_ALIASES:
                url = (host if host.endswith("/v1") else host + "/v1") + "/models"
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        return None
                    body = await resp.json()
                data = body.get("data", []) if isinstance(body, dict) else []
                return [str(m.get("id")) for m in data
                        if isinstance(m, dict) and m.get("id")]
            root = host[: -len("/v1")] if host.endswith("/v1") else host
            async with session.get(f"{root}/api/v0/models", headers=headers) as resp:
                if resp.status != 200:
                    return None
                body = await resp.json()
            data = body.get("data", []) if isinstance(body, dict) else []
            return [str(m.get("id")) for m in data
                    if isinstance(m, dict) and m.get("id") and m.get("state") == "loaded"]
    except Exception:  # noqa: BLE001 — a probe must never break the caller
        return None


class LiveSwitcher:
    """Owns the live-switch intent slot + the CURRENT session's store/seam.

    ``active`` is duck-typed on config_loader.ActiveConfig (character,
    model_name, voice_name, persona_name, model_id, temperature,
    reasoning_effort, voice_tag, ref_wav, reliable_context). ``reloader`` needs
    ``rebase()``; ``tts`` needs ``set_ref_wav()``; ``context`` needs
    ``messages`` + ``set_messages()`` — all duck-typed for testability.
    """

    def __init__(self, *, active, reloader, tts, context, store, seam,
                 lm_provider: str, lm_base_url: str, lm_token: str,
                 engine_info: Optional[dict] = None, recorder=None,
                 seam_factory=None, resident_probe=None) -> None:
        self._current = {
            "selection": {"character": active.character, "model": active.model_name,
                          "voice": active.voice_name, "persona": active.persona_name},
            "model_id": str(active.model_id),
            "temperature": float(active.temperature),
            "reasoning_effort": str(active.reasoning_effort),
            "reliable_context": getattr(active, "reliable_context", None),
            "voice_tag": str(active.voice_tag),
            "ref_wav": str(active.ref_wav),
        }
        self._reloader = reloader
        self._tts = tts
        self._context = context
        self._store = store
        self._seam = seam
        self._meter = None  # TokenMeter, attached late by bot.py (created after us)
        self._lm_provider = lm_provider
        self._lm_base_url = lm_base_url
        self._lm_token = lm_token
        self.engine_info = engine_info      # late-bound by bot.py main()
        self.recorder = recorder            # late-bound by bot.py main()
        self._seam_factory = seam_factory or _default_seam_factory
        self._resident_probe = resident_probe or (
            lambda: fetch_resident_ids(self._lm_provider, self._lm_base_url, self._lm_token))
        self.lm_model = str(active.model_id)  # the engine re-poll's live target
        self._pending: Optional[dict] = None
        self._preparing = False
        self._status: Optional[dict] = None
        self._finalize_task: Optional[asyncio.Task] = None

    # ── the shutdown path reads the CURRENT pieces from here ─────────────────

    @property
    def current_store(self):
        return self._store

    @property
    def current_seam(self):
        return self._seam

    def attach_meter(self, meter) -> None:
        """Late-bind the TokenMeter (duck-typed; this module stays pipecat-free)."""
        self._meter = meter

    def snapshot(self, messages) -> None:
        """Per-turn persistence hook target — always the CURRENT store."""
        st = self._store
        if st is not None:
            st.snapshot(messages)

    # ── status (names only — POL-GL-039) ─────────────────────────────────────

    async def describe(self) -> dict:
        resident = await self._resident_probe()
        pend = self._pending
        return {"ok": True,
                "armed": pend is not None,
                "pending": (dict(pend["selection"]) if pend else None),
                "current": dict(self._current["selection"]),
                "last": self._status,
                "resident_models": resident}

    # ── prepare: validate + eagerly build the bundle, then arm (last-wins) ───

    async def prepare(self, body: dict) -> dict:
        """POST /switch/live: arm a live switch. Heavy work (loads, memory
        attach, recall) runs HERE, off the event loop, so the turn-boundary
        apply is cheap. Refusals arm nothing. → {"ok", ...} (+ "code" on
        refusal: 400 = invalid/no-op, 409 = busy / residency)."""
        if self._preparing:
            return {"ok": False, "code": 409, "errors": ["a live switch is already preparing"]}
        target = switch_mod.merge_selection(dict(self._current["selection"]), body)
        mode = str(body.get("mode") or "new")
        name = (str(body["name"]) if body.get("name") else None)
        changed = [k for k in switch_mod.SELECTION_KEYS
                   if target[k] != self._current["selection"].get(k)]
        if not changed and mode != "resume":
            return {"ok": False, "code": 400,
                    "errors": ["nothing to change — that selection is already live"]}
        self._preparing = True
        try:
            errors = await asyncio.to_thread(switch_mod.validate_selection, dict(target))
            if errors:
                return {"ok": False, "code": 400, "errors": list(errors)}

            def _load_target():
                model = config_loader.load_model(target["model"])
                voice = config_loader.load_voice(target["character"], target["voice"])
                persona_slot = config_loader.compose_persona(target["character"], target["persona"])
                system = config_loader.compose_system_instruction(
                    target["model"], target["character"], persona=target["persona"])
                fingerprint = config_loader.compose_system_instruction(
                    target["model"], target["character"], persona=target["persona"],
                    datetime_str="")
                return model, voice, persona_slot, system, fingerprint

            try:
                model, voice, persona_slot, system, fingerprint = (
                    await asyncio.to_thread(_load_target))
            except config_loader.ConfigError as exc:
                return {"ok": False, "code": 400, "errors": [str(exc)]}

            new_model_id = str(model["id"])
            if new_model_id != self._current["model_id"]:
                resident = await self._resident_probe()
                if resident is None:
                    return {"ok": False, "code": 409, "errors": [
                        "cannot verify the target model is resident on the LLM server "
                        "(probe failed) — the restart path still works"]}
                if new_model_id not in resident:
                    return {"ok": False, "code": 409, "errors": [
                        f"model '{new_model_id}' is not resident on the LLM server "
                        "(M4c: live swap offers resident models only) — "
                        "the restart path still works"]}

            def _attach_and_resolve():
                seam = self._seam_factory(target["character"], target["persona"])
                try:
                    system_aug = seam.augment(system) if seam is not None else system
                    sdir = session_store.companion_sessions_dir(target["character"])
                    session_store.ensure_dir(sdir)
                    psha = session_store.prompt_sha256(fingerprint)
                    warnings: list = []
                    resume_messages = None
                    new_store = None
                    descriptor = "New"
                    if mode == "resume":
                        path = (session_store.resolve_resume_arg(name, sdir) if name
                                else (session_store.list_sessions(sdir)[0].path
                                      if session_store.list_sessions(sdir) else None))
                        if path is None:
                            warnings.append("resume: no matching session — starting fresh")
                        else:
                            try:
                                data = session_store.load(path)
                            except Exception as exc:  # noqa: BLE001 — never fail an arm on one bad file
                                warnings.append(f"resume: {path.name} unreadable "
                                                f"({type(exc).__name__}) — starting fresh")
                                data = None
                            if data is not None:
                                resume_messages = data.get("messages") or []
                                if str(data.get("persona") or "default") != target["persona"]:
                                    warnings.append("persona variant drift (resuming anyway)")
                                if data.get("model") and data["model"] != new_model_id:
                                    warnings.append("model drift (resuming anyway)")
                                if data.get("voice") and data["voice"] != voice["tag"]:
                                    warnings.append("voice drift (resuming anyway)")
                                if (data.get("prompt_sha256")
                                        and data["prompt_sha256"] != psha):
                                    warnings.append(
                                        "persona prompt changed since save (resuming anyway)")
                                new_store = session_store.SessionStore(
                                    session_id=path.stem, model=new_model_id,
                                    voice=str(voice["tag"]), prompt_sha256=psha,
                                    sessions_dir=sdir, character=target["character"],
                                    persona=target["persona"],
                                    started=data.get("started") or session_store._now_iso(),
                                    name=data.get("name"),
                                    held=bool(data.get("held", False)))
                                descriptor = ((new_store.name or new_store.session_id or "Held")
                                              if new_store.held else "Restored")
                    if new_store is None:
                        new_store = session_store.SessionStore(
                            session_id=session_store.new_session_id(),
                            model=new_model_id, voice=str(voice["tag"]),
                            prompt_sha256=psha, sessions_dir=sdir,
                            character=target["character"], persona=target["persona"])
                    return seam, system_aug, new_store, resume_messages, descriptor, warnings
                except BaseException:
                    if seam is not None:
                        try:
                            seam.close()
                        except Exception:  # noqa: BLE001
                            pass
                    raise

            seam, system_aug, new_store, resume_messages, descriptor, warnings = (
                await asyncio.to_thread(_attach_and_resolve))

            prior, self._pending = self._pending, None
            if prior is not None and prior.get("seam") is not None:
                await asyncio.to_thread(prior["seam"].close)  # superseded — release it
            self._pending = {
                "selection": target, "changed": changed, "mode": mode,
                "model_id": new_model_id,
                "temperature": float(model["temperature"]),
                "reasoning_effort": str(model["reasoning_effort"]),
                "reliable_context": model.get("reliable_context"),
                "voice_tag": str(voice["tag"]), "ref_wav": str(voice["ref_wav"]),
                "persona_slot": persona_slot, "system_instruction": system_aug,
                "seam": seam, "store": new_store, "resume_messages": resume_messages,
                "descriptor": descriptor,
                "hold": bool(body.get("hold")),
                "hold_name": (str(body["hold_name"]) if body.get("hold_name") else None),
                "warnings": warnings, "armed_at": _now_iso(),
            }
            self._status = {"phase": "armed", "to": dict(target),
                            "at": self._pending["armed_at"], "error": None}
            logger.info("[switch] live intent armed → {} (changed: {})",
                        {k: target[k] for k in switch_mod.SELECTION_KEYS}, changed)
            return {"ok": True, "armed": True, "to": dict(target), "changed": changed,
                    "warnings": warnings,
                    "applies": "at the next turn boundary — the next thing you say "
                               "lands with the new companion"}
        finally:
            self._preparing = False

    # ── apply: the atomic turn-boundary swap ─────────────────────────────────

    async def apply_pending(self):
        """Called by ConfigReloadProcessor on the turn's LLMContextFrame,
        BEFORE the overrides poll and BEFORE the frame forwards. Consumes the
        armed intent and returns the LLM delta as a plain dict
        {model, temperature, system_instruction, reasoning_effort} — or None
        when nothing is armed. Contained: a failure logs, records status, and
        leaves the loop running."""
        pending, self._pending = self._pending, None
        if pending is None:
            return None
        try:
            return await self._apply(pending)
        except Exception as exc:  # noqa: BLE001 — the loop must survive a bad swap
            logger.warning("[switch] live apply failed ({}) — loop continues",
                           type(exc).__name__)
            self._status = {"phase": "failed", "error": type(exc).__name__,
                            "to": dict(pending["selection"]), "at": _now_iso()}
            return None

    async def _apply(self, p: dict):
        sel = p["selection"]
        # 1. Split the context: the turn's trigger message (the user's words
        #    that arrived WITH this boundary) carries over to the new
        #    companion; everything before it belongs to the old session.
        msgs = list(self._context.messages)
        if msgs and isinstance(msgs[-1], dict) and msgs[-1].get("role") == "user":
            carry, old_msgs = [msgs[-1]], msgs[:-1]
        else:
            carry, old_msgs = [], msgs
        self._context.set_messages(list(p["resume_messages"] or []) + carry)
        if self._meter is not None:
            # Re-seed the runway gauge for the new companion's pre-fill; the
            # panel shows it estimated until their first turn reports.
            self._meter.prime_estimate(p["system_instruction"], self._context.messages)

        # 2. Voice re-clone (HIDEABLE ~0.2 s, masked under LLM think-time).
        voice_note = "unchanged"
        applied_ref = self._current["ref_wav"]
        if p["ref_wav"] != self._current["ref_wav"]:
            try:
                await asyncio.wrap_future(self._tts.set_ref_wav(p["ref_wav"]))
                applied_ref = p["ref_wav"]
                voice_note = "applied"
            except Exception as exc:  # noqa: BLE001 — degraded, never fatal
                voice_note = f"failed ({type(exc).__name__}) — previous voice kept"
                logger.warning("[switch] voice re-clone failed ({}) — previous voice kept",
                               type(exc).__name__)

        # 3. Session-scoped overrides die with the OLD session (a stale
        #    [voice].ref_wav audition must never override the switched voice at
        #    this same boundary's poll — the boot path scrubs identically).
        try:
            from hearth.control.features import config_knobs
            config_knobs.scrub_session_scoped()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[switch] session-scoped scrub failed ({})", type(exc).__name__)

        # 4. Rebase the reloader so the next overrides poll diffs against the
        #    NEW baselines (else it would fight the switch with stale ones).
        if self._reloader is not None:
            self._reloader.rebase(
                model_name=sel["model"],
                baseline_llm={"temperature": p["temperature"],
                              "reasoning_effort": p["reasoning_effort"],
                              "persona": p["persona_slot"]},
                baseline_voice=p["ref_wav"], applied_voice=applied_ref)

        # 5. Swap the current pieces; the snapshot hook and the shutdown path
        #    follow these references.
        old_store, old_seam = self._store, self._seam
        old_sel = dict(self._current["selection"])
        self._store, self._seam = p["store"], p["seam"]
        self._current = {
            "selection": dict(sel), "model_id": p["model_id"],
            "temperature": p["temperature"],
            "reasoning_effort": p["reasoning_effort"],
            "reliable_context": p["reliable_context"],
            "voice_tag": p["voice_tag"], "ref_wav": p["ref_wav"],
        }
        self.lm_model = p["model_id"]

        # 6. Panel facts (served by reference via /engine).
        ei = self.engine_info
        if ei is not None:
            ei["character"] = sel["character"]
            ei["voice"] = sel["voice"]
            ei["persona"] = sel["persona"]
            ei["session"] = p["descriptor"]
            ei["reliable"] = p["reliable_context"]
            ei["model_id"] = p["model_id"]  # provisional; next probe refreshes
        rec = self.recorder
        if rec is not None and not getattr(rec, "armed", False):
            try:
                rec.character = sel["character"]
                rec.base_dir = config_loader.companion_state_dir(sel["character"], "captures")
                if p["descriptor"] not in ("New", "Restored"):
                    from hearth.recording.recording import _slug
                    rec.default_name = _slug(p["descriptor"])
            except Exception as exc:  # noqa: BLE001
                logger.warning("[switch] recorder repoint failed ({})", type(exc).__name__)

        # 7. File discipline: converge active.toml on the applied selection
        #    (no-op when the daemon already wrote it — no .prev churn).
        try:
            current_file, err = switch_mod.read_selection()
            want = {k: sel[k] for k in switch_mod.SELECTION_KEYS}
            if err is not None or current_file is None or (
                    {k: current_file.get(k) for k in switch_mod.SELECTION_KEYS} != want):
                switch_mod.write_selection(want)
        except (ValueError, OSError) as exc:
            logger.warning("[switch] active.toml write failed ({}) — runtime switch stands; "
                           "file and runtime diverge until the next write", type(exc).__name__)

        # 8. Old-side finalize in the background: memory record + consolidate
        #    + the store's delete-decision (graceful-stop parity, incl. hold).
        loop = asyncio.get_running_loop()
        task = loop.create_task(asyncio.to_thread(
            self._finalize_old, old_store, old_seam, old_msgs,
            p["hold"], p["hold_name"]))
        status = {"phase": "applied", "at": _now_iso(), "from": old_sel,
                  "to": dict(sel), "voice": voice_note,
                  "warnings": p["warnings"], "old_session": "finalizing"}
        self._status = status

        def _done(t: asyncio.Task) -> None:
            try:
                res = t.result()
            except Exception as exc:  # noqa: BLE001
                res = f"failed ({type(exc).__name__})"
            status["old_session"] = res
            logger.info("[switch] previous session: {}", res)

        task.add_done_callback(_done)
        self._finalize_task = task
        logger.info("[switch] live switch applied → {} (voice: {})",
                    {k: sel[k] for k in switch_mod.SELECTION_KEYS}, voice_note)
        return {"model": p["model_id"], "temperature": p["temperature"],
                "system_instruction": p["system_instruction"],
                "reasoning_effort": p["reasoning_effort"]}

    @staticmethod
    def _finalize_old(store, seam, messages, hold: bool, hold_name) -> str:
        """Graceful-stop parity for the OLD session, off the event loop.
        Memory record FIRST (it must precede the store's ephemeral delete),
        then the delete-decision — hold honored via the same marker stop.sh
        uses. Returns a short human status; never raises."""
        try:
            parts = []
            if hold and store is not None:
                session_store.write_hold_request(hold_name, store.sessions_dir)
            if seam is not None:
                parts.append(seam.on_session_end(messages, store) or "")
                seam.close()
            if store is not None:
                parts.append(session_store.finalize(store, messages))
            return " · ".join(x for x in parts if x) or "no session"
        except Exception as exc:  # noqa: BLE001 — background tidy must never raise
            return f"finalize failed ({type(exc).__name__})"

    # ── shutdown hygiene ─────────────────────────────────────────────────────

    async def drain(self, timeout_s: float = 30.0) -> None:
        """Await an in-flight old-side finalize (shutdown calls this before the
        current session's own finalize so the two never interleave)."""
        task = self._finalize_task
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout_s)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[switch] old-session finalize still running at shutdown ({})",
                               type(exc).__name__)

    def close_pending(self) -> None:
        """Release an armed-but-never-applied intent's prepared seam."""
        p, self._pending = self._pending, None
        if p is not None and p.get("seam") is not None:
            try:
                p["seam"].close()
            except Exception:  # noqa: BLE001
                pass
