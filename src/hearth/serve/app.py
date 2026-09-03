"""serve/app.py — the /v1 facade application (a thin adapter).

Routes (bearer-authed except /health and the /admin/launch static shell):
    GET  /health                  liveness — {"ok": true}, no identity leaked
    GET  /v1/models               one entry: the active character (clients pick it)
    POST /v1/chat/completions     persona-composed chat → LM Studio, SSE passthrough
    POST /v1/audio/speech         the companion's voice — proxy to mlx_audio.server (:8555)
    POST /v1/audio/transcriptions local Whisper proxy (opt-in; default OFF)

Persona integrity: client system messages are DROPPED and Hearth's composed
system_instruction injected — a client can pick words, never who the companion is. The
speech route likewise pins model + ref_audio server-side and ignores client
"voice"/"model" fields. Model params (id, temperature, reasoning_effort) come
from the same ActiveConfig bot.py runs on, so chat-persona == voice-persona.

Identity pin (serve.toml [serve.identity]): when the optional table names a
character + voice, the facade's persona/voice resolve from THAT fixed selection
at start() — active.toml then supplies only the LLM leg (model id/params, whose
template still wraps the pinned persona). Closes both silent identity traps: a
standalone facade re-snapshotting a stale active.toml (a stale-identity
incident), and the in-process attach following the
live session's character. Absent table ⇒ legacy snapshot behavior, byte-same.
Exception: requests carrying X-Hearth-Internal: task are appliance-internal
utility calls (e.g. the voice server's rolling summarizer) — their own system
prompt is preserved, no persona is injected, and they are never taped.

Secrets: the facade bearer and the LM Studio key live only on the
deps object, resolved from env or *_source paths; never logged, echoed, or in a
response body.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional

import aiohttp
from aiohttp import web
from loguru import logger

from hearth.config import config_loader

from . import stt_prep, tts_prep
from .transcript import TranscriptTap

_REPO_ROOT = config_loader.DATA_DIR  # relative serve.toml paths resolve against the data root


@dataclass
class FacadeDeps:
    """Everything the handlers need, resolved once at start()."""

    system_instruction: str
    model_id: str
    temperature: float
    reasoning_effort: str
    character: str
    ref_wav: str
    tts_model: str
    lm_base_url: str
    lm_token: str
    bearer: str
    cfg: dict
    tap: Optional[TranscriptTap]
    pinned_tts: dict = field(default_factory=dict)
    # Tag-envelope policy: may per-tag knob profiles overlay the pinned knobs?
    # True when no pin exists; a pin must opt in via allow_tag_profiles = true.
    allow_tag_profiles: bool = True
    # Composition inputs for a CLIENT-DECLARED character (the /v1/models roster):
    # the active model's template name and the resolved identity's persona
    # variant, so a declared companion composes exactly as the pinned one does.
    model_name: str = ""
    persona: str = "default"
    # [serve.characters] — character name → its default voice bundle.
    characters: dict = field(default_factory=dict)
    # The facade-lane memory glue (serve/memory_glue.ServeMemory) or None when
    # [memory.serve] is absent/disabled. None ⇒ every path below is untouched.
    memory: Optional[object] = None
    # Resolution caches: declared character → (character, persona, instruction)
    # or None (a name that is not a character), and character → (ref_wav, model).
    identity_cache: dict = field(default_factory=dict)
    voice_cache: dict = field(default_factory=dict)
    # Per-identity transcript taps (misfiling fix 2026-08-31): the startup tap
    # serves the default character; a client-declared companion gets a tap homed
    # under her OWN directory, built by tap_factory and cached per character.
    tap_factory: Optional[object] = None
    tap_cache: dict = field(default_factory=dict)
    session: Optional[aiohttp.ClientSession] = field(default=None)


# ── secret resolution (paths/env in, values never out) ────────────────────────

def _resolve_bearer(cfg: dict) -> str:
    tok = os.environ.get("SERVE_TOKEN", "").strip()
    if tok:
        return tok
    src = Path(str(cfg.get("token_source", ""))).expanduser()
    if not src.is_absolute():
        src = _REPO_ROOT / src
    try:
        tok = src.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise config_loader.ConfigError(
            f"serve.toml token_source unusable ({type(exc).__name__}): {src}"
        )
    if not tok:
        raise config_loader.ConfigError(f"empty serve bearer token in {src}")
    return tok


def _resolve_lm_token(passed: str, cfg: dict) -> str:
    # "lm-studio" is bot.py's known-dead placeholder default, not a credential.
    if passed and passed != "lm-studio":
        return passed
    env = os.environ.get("LM_API_TOKEN", "").strip()
    if env and env != "lm-studio":
        return env
    src = Path(str(cfg.get("lm_token_source", ""))).expanduser()
    with contextlib.suppress(OSError):
        tok = src.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    return passed or "lm-studio"  # requests will 401 upstream → surfaced as 502-class errors


# ── auth middleware ───────────────────────────────────────────────────────────

# Unauthed paths: liveness, plus the supervisor's static SHELLS — the launch
# page, the roster wizard, the memory review-and-prune pane, and the generated
# settings forms: contentless chrome (no names, no state, no tokens baked in)
# whose every data call comes back through this middleware with the bearer.
# Nothing else is ever exempted; when the supervisor isn't mounted, the /admin
# pages are 404s.
_AUTH_EXEMPT = frozenset({"/health", "/admin/launch", "/admin/roster",
                          "/admin/memory/ui", "/admin/settings/ui"})


# The browser carrier. A top-level navigation cannot attach an Authorization
# header, so the proxied control panel is unreachable from a browser on the
# header alone. A page that already holds the bearer mints this cookie through
# an authed POST /admin/cookie; it is DERIVED from the bearer rather than being
# it, so the raw secret never sits in a cookie jar and the cookie's value can
# never be replayed as a header. Same power, different carrier — and it dies
# the moment the bearer is rotated, with no server-side session state.
COOKIE_NAME = "hearth_facade"
_COOKIE_LABEL = b"hearth-facade-cookie-v1"


def cookie_value(bearer: str) -> str:
    """The cookie that stands in for ``bearer`` on a browser navigation."""
    return hmac.new(bearer.encode(), _COOKIE_LABEL, hashlib.sha256).hexdigest()


@web.middleware
async def _auth(request: web.Request, handler):
    if request.path in _AUTH_EXEMPT:
        return await handler(request)
    bearer = request.app["deps"].bearer
    supplied = request.headers.get("Authorization", "")
    if hmac.compare_digest(supplied.encode(), ("Bearer " + bearer).encode()):
        return await handler(request)
    cookie = request.cookies.get(COOKIE_NAME, "")
    if cookie and hmac.compare_digest(cookie.encode(), cookie_value(bearer).encode()):
        return await handler(request)
    return web.json_response({"error": "unauthorized"}, status=401)


# ── helpers ───────────────────────────────────────────────────────────────────

def _text_of(content) -> str:
    """Flatten OpenAI message content (str, or list of typed parts) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(p.get("text", "")) for p in content if isinstance(p, dict) and p.get("type") == "text"
        ).strip()
    return ""


def _sse_text(raw: bytes) -> str:
    """Concatenate the delta.content of an accumulated SSE stream (for the tap)."""
    parts: list[str] = []
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            break
        try:
            delta = json.loads(payload)["choices"][0].get("delta", {})
        except (ValueError, KeyError, IndexError, TypeError):
            continue
        if delta.get("content"):
            parts.append(delta["content"])
    return "".join(parts)


def _declared_identity(deps: "FacadeDeps", name: str):
    """(character, persona, system_instruction) for a client-declared character.

    The client picks from /v1/models, and a name that resolves to a REAL
    character on this machine is honored — the bearer already grants access to
    the facade, and identity is what memory attribution follows, so a walk with
    one companion must not file under another. Anything else (a model id, a
    typo, a traversal attempt) returns None and the caller keeps the identity
    start() resolved. Both answers are cached: composition reads persona files.
    """
    if not name or name == deps.character:
        return None
    if name in deps.identity_cache:
        return deps.identity_cache[name]
    identity = None
    try:
        if config_loader._NAME_RE.match(name) and not name.startswith("."):
            if config_loader.persona_path(name).is_file():
                identity = (name, "default",
                            config_loader.compose_system_instruction(deps.model_name, name))
    except Exception as exc:  # noqa: BLE001 — an unknown name is not an error
        logger.warning("[serve] declared character {!r} unusable ({}) — using the "
                       "resolved identity", name, type(exc).__name__)
        identity = None
    deps.identity_cache[name] = identity
    if identity is not None:
        logger.info("[serve] client-declared character: {}", name)
    return identity


def _voice_deps(deps: "FacadeDeps", body: dict) -> "FacadeDeps":
    """deps, or a copy carrying a declared character's own voice bundle.

    A client that names a character listed in [serve.characters] gets THAT
    character's mapped bundle for this request; every other request keeps the
    pinned voice byte-for-byte (the client "voice"/"model" fields stay ignored,
    as they always were, for anything not on the roster).
    """
    name = str(body.get("voice") or body.get("model") or "").strip()
    if not name or name not in deps.characters:
        return deps
    if name in deps.voice_cache:
        resolved = deps.voice_cache[name]
    else:
        resolved = None
        try:
            bundle = config_loader.load_voice(name, str(deps.characters[name]))
            resolved = (bundle["ref_wav"],
                        str(bundle.get("model_repo") or deps.cfg["tts_model"]))
        except Exception as exc:  # noqa: BLE001 — a bad bundle costs the pin, not the reply
            logger.warning("[serve] voice bundle for {!r} unusable ({}) — pinned voice kept",
                           name, type(exc).__name__)
        deps.voice_cache[name] = resolved
    if resolved is None:
        return deps
    return replace(deps, ref_wav=resolved[0], tts_model=resolved[1])


# ── handlers ──────────────────────────────────────────────────────────────────

async def _health(_req: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def _models(request: web.Request) -> web.Response:
    deps: FacadeDeps = request.app["deps"]
    # The resolved identity first, then [serve.characters] — deduped, order kept.
    # A client can only declare a companion it was offered here.
    names = list(dict.fromkeys([deps.character, *deps.characters]))
    return web.json_response(
        {"object": "list",
         "data": [{"id": name, "object": "model", "created": 0, "owned_by": "hearth"}
                  for name in names]}
    )


async def _chat(request: web.Request) -> web.StreamResponse:
    deps: FacadeDeps = request.app["deps"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": {"message": "invalid JSON body"}}, status=400)

    stream = bool(body.get("stream", False))
    # Appliance-internal utility calls (rolling summary, etc.) mark themselves
    # with X-Hearth-Internal: task. Their system prompt IS the task, so persona
    # injection would corrupt them — a summarizer becomes the persona answering
    # its own transcript. Pass them through verbatim, keep them out of the tap,
    # and honor their temperature. Bearer auth still gates every caller; this
    # header only changes composition, never who can reach the facade.
    internal = request.headers.get("X-Hearth-Internal") == "task"
    # Channel truth for the tap: a streaming client stamps "voice"; the tap
    # whitelists the value, anything else files as the default "chat".
    tap_channel = request.headers.get("X-Hearth-Channel")
    last_user = ""
    # Whose conversation is this? A client that names a real character in
    # `model` gets THAT companion (it picked from the /v1/models roster);
    # anything else keeps the identity start() resolved. This is also the memory
    # attribution — records file under the companion who actually spoke.
    character, persona, instruction = deps.character, deps.persona, deps.system_instruction
    hint = request.headers.get("X-Hearth-Session", "")
    if internal:
        messages = [m for m in body.get("messages") or [] if isinstance(m, dict)]
    else:
        declared = _declared_identity(deps, str(body.get("model") or "").strip())
        if declared is not None:
            character, persona, instruction = declared
        client_turns = []
        for m in body.get("messages") or []:
            if not isinstance(m, dict) or m.get("role") == "system":
                continue  # persona integrity: the facade owns the system layer
            client_turns.append(m)
            if m.get("role") == "user":
                last_user = _text_of(m.get("content"))
        if deps.memory is not None:
            # Opens the conversation on its first turn (recall paid once) and
            # returns the AUGMENTED instruction; later turns are a dict lookup —
            # unless [memory.per_turn] is enabled, where the user's own words
            # (the cue) can add a targeted recall to THIS request's instruction.
            instruction = await deps.memory.instruction(
                character, persona, tap_channel, hint, instruction, cue=last_user)
        messages = [{"role": "system", "content": instruction}, *client_turns]

    out = {
        "model": deps.model_id,
        "messages": messages,
        # Live-layer parity: panel-written overrides.toml wins, as on the desk.
        "temperature": (body["temperature"] if internal and "temperature" in body
                        else tts_prep.live_llm_temperature(deps.temperature)),
        "stream": stream,
    }
    if deps.reasoning_effort:
        # Body-level reasoning_effort is the ONE field this LM Studio build maps
        # to the template's enable_thinking var (bot.py wiring note) — without it
        # hybrid-thinking models starve the reply into reasoning_content.
        out["reasoning_effort"] = deps.reasoning_effort
    for k in ("max_tokens", "max_completion_tokens", "stop", "stream_options"):
        if k in body:
            out[k] = body[k]

    url = deps.lm_base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {deps.lm_token}"}
    try:
        upstream = await deps.session.post(
            url, json=out, headers=headers,
            timeout=aiohttp.ClientTimeout(total=600, sock_connect=5),
        )
    except aiohttp.ClientError as exc:
        return web.json_response(
            {"error": {"message": f"LLM upstream unreachable ({type(exc).__name__})"}}, status=502
        )

    if upstream.status != 200:
        text = await upstream.text()
        upstream.release()
        return web.Response(text=text[:2000], status=upstream.status, content_type="application/json")

    if not stream:
        data = await upstream.json()
        upstream.release()
        reply = ""
        with contextlib.suppress(KeyError, IndexError, TypeError):
            reply = data["choices"][0]["message"]["content"] or ""
        tap = _tap_for(deps, character)
        if tap and last_user and reply:
            tap.record(last_user, reply, channel=tap_channel)
        if deps.memory is not None and not internal and last_user and reply:
            deps.memory.note_exchange(character, tap_channel, hint, last_user, reply)
        return web.json_response(data)

    # SSE passthrough: bytes forwarded verbatim; a copy accumulates for the tap.
    resp = web.StreamResponse(
        status=200,
        headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"},
    )
    await resp.prepare(request)
    raw = bytearray()
    try:
        async for chunk in upstream.content.iter_any():
            raw.extend(chunk)
            await resp.write(chunk)
    finally:
        upstream.release()
    with contextlib.suppress(Exception):
        await resp.write_eof()
    reply = _sse_text(bytes(raw))
    tap = _tap_for(deps, character)
    if tap and last_user and reply:
        tap.record(last_user, reply, channel=tap_channel)
    if deps.memory is not None and not internal and last_user and reply:
        deps.memory.note_exchange(character, tap_channel, hint, last_user, reply)
    return resp


def _tap_for(deps: "FacadeDeps", character: str) -> Optional[TranscriptTap]:
    """The transcript tap for the RESOLVED companion (misfiling fix 2026-08-31):
    the startup tap serves the default identity; a client-declared character
    files under her own transcripts directory, one cached tap per character."""
    if character == deps.character or deps.tap_factory is None:
        return deps.tap
    tap = deps.tap_cache.get(character)
    if tap is None:
        tap = deps.tap_factory(character)
        deps.tap_cache[character] = tap
    return tap


async def _speech(request: web.Request) -> web.StreamResponse:
    deps: FacadeDeps = request.app["deps"]
    if not deps.cfg.get("speech_enabled", True):
        return web.json_response({"error": "speech disabled (serve.toml speech_enabled)"}, status=403)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON body"}, status=400)
    # Parity prep: paralinguistic repair/strip + live knob forwarding; voice
    # identity pinned server-side (client "model"/"voice" ignored). See tts_prep.
    speech_deps = _voice_deps(deps, body)
    payload, err = tts_prep.build_speech_payload(speech_deps, body)
    if payload is None:
        if err:
            return web.json_response({"error": err}, status=400)
        # Word-less fragment: silence is the faithful rendering, not an error.
        return web.Response(body=tts_prep.SILENT_WAV, content_type="audio/wav")

    url = str(deps.cfg["audio_base_url"]).rstrip("/") + "/audio/speech"

    if payload.get("stream"):
        # M6 live path: relay the chunked-envelope stream untouched. One upstream
        # call ⇒ the tag envelope (if any) covers this whole call.
        try:
            upstream = await deps.session.post(
                url, json=tts_prep.with_tag_profile(payload, speech_deps),
                timeout=aiohttp.ClientTimeout(total=300, sock_connect=5)
            )
        except aiohttp.ClientError as exc:
            return web.json_response({"error": f"TTS upstream unreachable ({type(exc).__name__})"}, status=502)
        if upstream.status != 200:
            err = await upstream.text()
            upstream.release()
            return web.Response(text=err[:2000], status=upstream.status)

        resp = web.StreamResponse(
            status=200,
            headers={"Content-Type": upstream.headers.get("Content-Type", "audio/wav")},
        )
        await resp.prepare(request)
        try:
            async for chunk in upstream.content.iter_any():
                await resp.write(chunk)
        finally:
            upstream.release()
        with contextlib.suppress(Exception):
            await resp.write_eof()
        return resp

    # Voice-note path (M8): sentence-sized chunk requests, stitched to one
    # complete WAV — the stable regime; see tts_prep.sentence_chunks rationale.
    blobs: list[bytes] = []
    for chunk_text in tts_prep.sentence_chunks(payload["input"]):
        try:
            # Tag envelope per CHUNK: a style tag elevates only the sentence-chunk
            # that carries it; the next chunk falls back to the base payload.
            upstream = await deps.session.post(
                url, json=tts_prep.with_tag_profile(dict(payload, input=chunk_text), speech_deps),
                timeout=aiohttp.ClientTimeout(total=300, sock_connect=5),
            )
        except aiohttp.ClientError as exc:
            return web.json_response({"error": f"TTS upstream unreachable ({type(exc).__name__})"}, status=502)
        if upstream.status != 200:
            err = await upstream.text()
            upstream.release()
            return web.Response(text=err[:2000], status=upstream.status)
        blobs.append(await upstream.read())
    try:
        stitched = tts_prep.concat_wavs(blobs)
    except ValueError as exc:
        return web.json_response({"error": f"TTS upstream returned non-WAV audio ({exc})"}, status=502)
    return web.Response(body=stitched, content_type="audio/wav")


async def _transcriptions(request: web.Request) -> web.Response:
    deps: FacadeDeps = request.app["deps"]
    if not deps.cfg.get("transcriptions_enabled", False):
        # Input-side voice notes are opt-in — OFF until the operator enables them.
        return web.json_response(
            {"error": "voice-note input disabled (serve.toml transcriptions_enabled)"}, status=403
        )
    form = await request.post()
    up = form.get("file")
    if up is None or not getattr(up, "file", None):
        return web.json_response({"error": "multipart 'file' field required"}, status=400)

    try:
        wav = await stt_prep.to_clean_wav(up.file.read())
    except stt_prep.AudioDecodeError as exc:
        return web.json_response({"error": f"could not decode audio ({exc})"}, status=400)

    if stt_prep.is_silence(wav):
        # Whisper invents speech on room tone ("Thank you." / "Gracias") —
        # answer empty ourselves rather than relay its imagination.
        return web.json_response({"text": ""})

    data = aiohttp.FormData()
    data.add_field("file", wav, filename="audio.wav", content_type="audio/wav")
    data.add_field("model", str(deps.cfg["stt_model"]))
    data.add_field("response_format", "json")  # mlx-audio defaults to ndjson otherwise
    if form.get("language"):
        data.add_field("language", str(form["language"]))

    url = str(deps.cfg["audio_base_url"]).rstrip("/") + "/audio/transcriptions"
    try:
        async with deps.session.post(
            url, data=data, timeout=aiohttp.ClientTimeout(total=300, sock_connect=5)
        ) as upstream:
            payload = await upstream.text()
            return web.Response(text=payload[:100_000], status=upstream.status,
                                content_type="application/json")
    except aiohttp.ClientError as exc:
        return web.json_response({"error": f"STT upstream unreachable ({type(exc).__name__})"}, status=502)


# ── app factory + lifecycle ───────────────────────────────────────────────────

def build_app(deps: FacadeDeps) -> web.Application:
    # 32 MB body cap: raised from aiohttp's 1 MB default for the roster
    # wizard's sample upload. The auth middleware answers 401 before any
    # handler reads a body, so the widened cap is reachable only through the
    # bearer door (and the loopback/overlay bind is the outer wall).
    app = web.Application(middlewares=[_auth], client_max_size=32 * 1024**2)
    app["deps"] = deps
    app.router.add_get("/health", _health)
    app.router.add_get("/v1/models", _models)
    app.router.add_post("/v1/chat/completions", _chat)
    app.router.add_post("/v1/audio/speech", _speech)
    app.router.add_post("/v1/audio/transcriptions", _transcriptions)

    async def _open(app_: web.Application) -> None:
        app_["deps"].session = aiohttp.ClientSession()

    async def _close(app_: web.Application) -> None:
        if app_["deps"].session is not None:
            await app_["deps"].session.close()

    async def _memory_up(app_: web.Application) -> None:
        try:
            await app_["deps"].memory.start()
        except Exception as exc:  # noqa: BLE001 — memory never costs the facade
            logger.warning("[serve] memory glue start failed ({})", type(exc).__name__)

    async def _memory_down(app_: web.Application) -> None:
        # Ordered before _close on purpose: every open conversation becomes a
        # record while the process is still healthy — a bootout writes records,
        # not orphans.
        try:
            await app_["deps"].memory.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[serve] memory glue stop failed ({})", type(exc).__name__)

    app.on_startup.append(_open)
    if deps.memory is not None:
        app.on_startup.append(_memory_up)
        app.on_cleanup.append(_memory_down)
    app.on_cleanup.append(_close)
    return app


async def start(active, cfg: dict, lm_base_url: str, lm_token: str,
                mount=None) -> Optional[web.AppRunner]:
    """Bind the facade per serve.toml. Config problems raise (fail-fast, naming
    the file/path); a busy port warns and returns None — the caller (the voice
    appliance) must survive a standalone facade already holding the socket."""
    # Identity resolution: [serve.identity] pin wins; else snapshot the active
    # selection (legacy). The pinned persona is composed through the ACTIVE
    # model's template — hard rules stay pinned by construction, only the
    # {{persona}} body changes (compose_with_persona contract).
    ident = cfg.get("identity")
    if ident:
        character = str(ident["character"])
        persona_name = str(ident.get("persona") or "default")
        pinned_voice = config_loader.load_voice(character, str(ident["voice"]))
        system_instruction = config_loader.compose_system_instruction(
            active.model_name, character, persona=persona_name)
        ref_wav = pinned_voice["ref_wav"]
        tts_model = str(pinned_voice.get("model_repo") or cfg["tts_model"])
        # Optional TTS knob pin: pinned keys win over the shared live layer
        # (overrides.toml [tts]) for facade speech only. Validated against the
        # same allowlist the live layer uses — a typo'd knob fails the start
        # loudly instead of silently not pinning.
        pinned_tts = dict(ident.get("tts") or {})
        # `allow_tag_profiles` is a pin POLICY flag, not a knob — pop before the
        # allowlist check and never forward it upstream. True ⇒ the tag envelope
        # (tts_prep.with_tag_profile) may overlay the pinned knobs; default False
        # keeps the pin absolute (it exists to stop knob drift).
        allow_tag_profiles = bool(pinned_tts.pop("allow_tag_profiles", False))
        unknown = set(pinned_tts) - tts_prep._SPEECH_KNOBS
        if unknown:
            raise config_loader.ConfigError(
                f"[serve.identity.tts] unknown knob(s) {sorted(unknown)} — "
                f"valid: {sorted(tts_prep._SPEECH_KNOBS)}")
        knob_note = f" tts-pin={pinned_tts}" if pinned_tts else ""
        pin_note = f", voice={ident['voice']} [pinned]{knob_note}"
    else:
        character = active.character
        persona_name = active.persona_name
        system_instruction = active.system_instruction
        ref_wav = active.ref_wav
        tts_model = str(active.model_repo or cfg["tts_model"])
        pinned_tts = {}
        allow_tag_profiles = True  # no pin ⇒ nothing to protect; envelope applies freely
        pin_note = ""
    tap = None
    tap_factory = None
    if cfg.get("transcript_tap", True):
        tdir_raw = Path(str(cfg["transcript_dir"])).expanduser()

        def tap_factory(c: str, _raw=tdir_raw, _model=active.model_id) -> TranscriptTap:
            # relative ⇒ inside EACH companion's own directory (per-identity taps)
            home = _raw if _raw.is_absolute() else config_loader.companion_state_dir(c, str(_raw))
            return TranscriptTap(home, c, channel="chat", model=_model)

        tap = tap_factory(character)
    # The facade-lane memory glue: OFF unless [memory.serve] enabled = true.
    # Absent or disabled, nothing is imported and every path above behaves
    # exactly as it did before the seam existed (the house gate idiom).
    glue = None
    mem_cfg = config_loader.load_memory_config()
    if mem_cfg and dict(mem_cfg.get("serve") or {}).get("enabled"):
        from .memory_glue import ServeMemory  # lazy: the seam loads only past the gate

        glue = ServeMemory(mem_cfg)
    deps = FacadeDeps(
        system_instruction=system_instruction,
        model_id=active.model_id,
        temperature=active.temperature,
        reasoning_effort=active.reasoning_effort,
        character=character,
        ref_wav=ref_wav,
        tts_model=tts_model,
        lm_base_url=(lm_base_url or str(cfg["lm_base_url"])),
        lm_token=_resolve_lm_token(lm_token, cfg),
        bearer=_resolve_bearer(cfg),
        cfg=cfg,
        tap=tap,
        tap_factory=tap_factory,
        pinned_tts=pinned_tts,
        allow_tag_profiles=allow_tag_profiles,
        model_name=active.model_name,
        persona=persona_name,
        characters=dict(cfg.get("characters") or {}),
        memory=glue,
    )
    app = build_app(deps)
    if mount is not None:
        mount(app)  # [serve.supervisor] — the daemon face; joins BEFORE setup
    runner = web.AppRunner(app)
    await runner.setup()
    host, port = str(cfg["host"]), int(cfg["port"])
    try:
        site = web.TCPSite(runner, host, port)
        await site.start()
    except OSError as exc:
        logger.warning("[serve] bind {}:{} failed ({}) — facade NOT attached "
                       "(standalone `python -m hearth.serve` already running?)", host, port, exc)
        await runner.cleanup()
        return None
    mem_note = ", memory=on" if glue is not None else ""
    print(f"[serve] /v1 facade → http://{host}:{port}/v1 "
          f"(character={character}{pin_note}{mem_note})", flush=True)
    return runner
