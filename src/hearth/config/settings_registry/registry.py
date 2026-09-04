"""settings_registry/registry.py — FileEntry, the REGISTRY itself, and ENV_VARS.

Sliced out of the single settings_registry.py it used to share; see the
package __init__ for the layout and the order the parts import in.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from .schema_files import ActiveFile, ModelFile, OverridesFile, ProfileFile, TtsBaselineFile, VadFile, VoiceFile
from .schema_tables import MemoryTable, OpenclawTable, ServeTable

# ── the registry ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FileEntry:
    kind: str
    model: type[BaseModel]
    title: str
    path: str          # human-readable location pattern (data root first)
    role: str          # selection | load facts | descriptor | live overrides | calibration | gate | preset
    owner: str         # operator | panel | shipped
    layer: str         # place | model | identity  (companion data-root scopes)
    restart: str       # what must relaunch for a persisted edit to land: none | bot | facade | bot+facade
    top_key: str | None = None  # gate files: the single top-level table
    note: str = ""


REGISTRY: dict[str, FileEntry] = {e.kind: e for e in (
    FileEntry("active", ActiveFile, "The selection pointer", "config/active.toml",
              "selection", "operator", "place", "bot+facade",
              note="Your one deliberate lever for who is live. Read once at startup; the "
                   "supervisor's switch button writes it and applies it live at the next turn "
                   "boundary (or via a warm restart) — hand-edit + restart keeps working. The "
                   "facade re-reads at kickstart (a [serve.identity] pin keeps its own voice "
                   "regardless)."),
    FileEntry("model", ModelFile, "Model load facts", "config/models/<model>/model.toml",
              "load facts", "operator", "model", "bot",
              note="Per-model request facts. context_length is deliberately absent — the live "
                   "server's loaded value wins. The facade re-reads at kickstart."),
    FileEntry("voice", VoiceFile, "Voice bundle descriptor", "characters/<character>/voices/<voice>/voice.toml",
              "descriptor", "operator", "identity", "bot",
              note="A voice is a self-contained bundle: descriptor + reference clip in one "
                   "directory. The clip conditions once at startup."),
    FileEntry("overrides", OverridesFile, "The live override layer", "config/overrides.toml",
              "live overrides", "panel", "place", "none",
              note="PANEL-MANAGED. Polled every turn boundary; values overlay the baselines "
                   "(delete a key and it reverts). [voice].ref_wav is session-scoped."),
    FileEntry("tts-baseline", TtsBaselineFile, "TTS engine baseline", "config/tts/<engine>/tts.toml",
              "calibration", "shipped", "place", "bot",
              note="Every [live] value equals the engine's own default (machine-checked no-op "
                   "guarantee). [tag_profiles.*] deltas are ear-calibrated — change by listening. "
                   "A data-root copy wins whole-file. The facade re-reads per speech request."),
    FileEntry("vad", VadFile, "Listening calibration", "config/vad.toml",
              "calibration", "shipped", "place", "bot",
              note="Mic, room, and speech-habit calibration — plumbing, never character texture; "
                   "profiles never carry it. A data-root copy wins whole-file."),
    FileEntry("serve", ServeTable, "The serve-facade gate", "config/serve.toml",
              "gate", "operator", "place", "facade", top_key="serve",
              note="Holds a bearer-token PATH: manage it, never print it. Off ⇒ byte-identical "
                   "appliance, no socket."),
    FileEntry("memory", MemoryTable, "The memory-seam gate", "config/memory.toml",
              "gate", "operator", "place", "bot+facade", top_key="memory",
              note="Cross-session continuity per companion. Records are the truth; backends are "
                   "derived indexes (`forget --session <id>` deletes one conversation from both; "
                   "see docs/memory.md)."),
    FileEntry("openclaw", OpenclawTable, "The OpenClaw-bridge gate", "config/openclaw.toml",
              "gate", "operator", "place", "bot", top_key="openclaw",
              note="One gate drives tool registration AND the {{openclaw_tools}} prompt slot, so "
                   "capability and prompt can never disagree."),
    FileEntry("profile", ProfileFile, "Companion knob presets", "characters/<c>[/voices/<v>]/profile.toml (+ overrides.toml mirrors)",
              "preset", "panel", "identity", "none",
              note="PANEL-MANAGED snapshots of the override deltas for one companion or voice; "
                   "they travel with the companion's directory. An empty preset == baseline. One "
                   "key is yours, not the panel's: `voice` in a CHARACTER profile pins the bundle "
                   "the switch pickers offer when you move to that character — hand-edit it, and a panel save "
                   "carries it through untouched."),
)}


# Environment variables the engine READS (documented here; validated nowhere).
ENV_VARS: tuple[tuple[str, str, str, str], ...] = (
    ("HEARTH_ROOT", "the checkout (found from the package)", "config_loader", "engine-tree anchor"),
    ("HEARTH_DATA", "HEARTH_ROOT", "config_loader", "data root — everything the operator owns"),
    ("WEB_HOST", "127.0.0.1", "control panel", "panel bind address (0.0.0.0 = LAN)"),
    ("WEB_PORT", "65000", "control panel", "panel port"),
    ("LM_BASE_URL", "http://127.0.0.1:8080/v1", "pipeline (bot)", "LLM server endpoint"),
    ("LM_API_TOKEN", "none", "pipeline (bot)", "LLM bearer key, only if the server wants one"),
    ("LM_PROVIDER", "llama-server", "pipeline + panel", "which engine probe the panel uses (llama-server | lmstudio)"),
    ("T4_METRICS", "0", "pipeline (bot)", "1 = per-turn latency marks in the log"),
    ("HEARTH_DEV_RELOAD", "0", "control panel + facade",
     "1 = re-read page files per request (dev; default reads once at import)"),
    ("SERVE_TOKEN", "(unset)", "serve facade", "facade bearer override — wins over token_source"),
)


