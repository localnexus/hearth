# Switching who's live — character, voice, model

*The ritual for changing **who** answers on the phone-voice and chat lanes: pick the companion in one file, make sure
no one's mid-conversation, bounce one service, confirm the right one came back.*

**Authoritative sources:** what each config key means → `../../docs/config-manual/llm.md` + `../../docs/config-manual/voice-tts.md`;
the desk-loop restart drill → `../../docs/runbook/` §2–3. This page is the *sequence* that ties them together.

---

## What owns "who's live"

The **serve facade** (:65001) reads `config/active.toml` **once, at its own startup**, and pins that
character + voice for every chat turn and every voice note it serves. Nothing is hot-swapped. So switching
the companion is always the same shape: **edit the pointer → bounce the facade → confirm**.

> **One file, three keys.** `config/active.toml` holds `character`, `model`, and `voice`. It's a
> **skip-worktree local float** — your live selection, never committed. Edit it freely; you're not dirtying
> the repo.

---

## The four steps

### 1 · Edit the selection

Open `config/active.toml` and set what you want:

```toml
character = "<name>"     # a dir under characters/
model     = "<name>"     # a dir under config/models/
voice     = "<tag>"      # a bundle under characters/<name>/voices/
```

Change one or all three. (Adding a *new* character or voice first? See
[Onboarding a character](onboarding-a-character.md).)

### 2 · Confirm no one is mid-conversation — **do this before you bounce**

Bouncing the facade **kills any live phone-voice session in progress.** Check it's empty first:

```bash
lsof -nP -iTCP:8080 -iTCP:3478 -iTCP:65001 -sTCP:ESTABLISHED
```

**Empty output = safe to switch.** Any established connection = someone (probably you, on a walk) is
talking to the companion right now — **wait**. Never bounce out from under a
live session.

### 3 · Bounce the facade

```bash
launchctl kickstart -k gui/$(id -u)/com.hearth.facade
```

`-k` kills the running facade and starts it fresh, so it re-reads `active.toml`. It comes back within a
couple of seconds. (This is the *facade* — the desk voice loop and the phone are untouched by it; see the
lanes note below.)

### 4 · Confirm the right one came back

Two agreeing checks — the facade should now *advertise* the new character, and its log should *say* it
switched:

```bash
curl -s -H "Authorization: Bearer $(cat config/serve-token)" http://127.0.0.1:65001/v1/models
```
The returned model `id` is the active character — it should be your new pick. Then the freshest line in
`logs/serve-facade.log` agrees:
```
[serve] /v1 facade → http://127.0.0.1:65001/v1 (character=<name>)
```
When those two match your edit, the switch took.

---

## The gotchas — the things the checks won't tell you

- **The voice is the one thing the log can't confirm.** The facade log names the **character**, never the
  **voice bundle**. There's no read-out for "which `.wav` is the companion cloning." The only check that matters is
  your **ear** — listen to the companion's first spoken reply and make sure it's the voice you meant.
- **`serve.toml` `enabled = false` means the facade won't run at all.** If the bounce is followed by
  *connection refused* and the log says nothing loaded, that's the **gate**, not a fault — the facade is
  designed to exit and open no socket when disabled. (See [When it misbehaves](when-it-misbehaves.md).)
- **The away-mode media server does NOT switch voices.** Restarting the away-mode media server (:8080) changes *nothing*
  about who the companion is — persona and voice for the voice lane are owned by the **facade**. Bounce the facade, not the
  away-mode media server.
- **The `:3001` voice client needs nothing.** It just reconnects through the facade; no restart, no reload.

---

## What each lane does when you switch

Three lanes read `active.toml` at *different* moments, so a switch reaches them differently:

| Lane | How it learns the new pick | What you do |
|---|---|---|
| **Phone voice + chat** (via the facade) | Snapshots `active.toml` when the **facade** starts | The bounce in step 3 — that's the whole point |
| **Open WebUI** (:65002 chat) | Reads the facade's `/v1/models` fresh on page load | **No restart.** Refresh the page / start a new chat and the new character id shows. **[UNVERIFIED]** old chats appear to stay pinned to whatever id they opened with (standard per-conversation model pinning — confirm by eye) |
| **Desk voice loop** (`bot.py`, :65000) | Snapshots `active.toml` at **its own** launch | Separate lane entirely — a facade bounce doesn't touch a running desk loop. To move *it*, stop and relaunch `bot.py` (`../../docs/runbook/` §3→§2) |

> **The mental model:** an interactively-launched `bot.py` and the launchd facade are *two independent
> readers* of the same selection file. Each takes its snapshot when it starts. Switching the facade never
> disturbs a desk session, and vice-versa.

**Net:** edit `active.toml`, check the ESTABLISHED probe is empty, `kickstart -k` the facade, then let the
`/v1/models` id, the log line, and your ear all agree.
