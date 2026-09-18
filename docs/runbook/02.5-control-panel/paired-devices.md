# 2.5f Paired devices — the list a conversation's audio can be sent to

> Part of [2.5 Control panel & live status](../02.5-control-panel.md). The route
> itself — the socket, the hello, the buffer depths and the wait — is
> [the remote audio route](remote-audio-route.md).

Pairing a device **enrols** it: one row, in `HEARTH_DATA/config/devices.toml`.

```toml
[[device]]
id = "pixel-3f4a"
label = "Pixel"
paired_at = "2026-09-18T04:10:11-07:00"
last_seen = "2026-09-18T04:10:11-07:00"
```

| field | what it is |
|---|---|
| `id` | the word a route carries — `remote:pixel-3f4a`. A slug of the label plus four hex characters, so two phones called "Pixel" are two devices |
| `label` | what you call it, in your own words. Trimmed to 40 characters; never an identifier |
| `paired_at` | when it first traded a code for the key. A re-pair does not move it |
| `last_seen` | the last time **a conversation was started for it**, or the last time it paired again. Not a heartbeat and not a connection — nothing on a hot path writes this file |

Two acts write it — the claim on the pairing page, and **forget** on the launch
page — plus one `last_seen` stamp per conversation. Every write goes to a `.tmp`
beside it and is moved onto the name, so nothing ever reads half a list. A file
that cannot be parsed is **no devices and one warning**, never a conversation
that refuses to start.

## Pairing, and what it now answers

The pairing page asks **what to call this device** above the six digits,
prefilled from what the browser says it is (a Pixel, an iPhone, a Mac…) and
entirely editable. The claim carries it:

```json
{"code": "123456", "label": "Pixel", "device_id": "<what this device already is, if anything>"}
```

and answers the key **plus the name the device is known by from then on**:

```json
{"token": "…", "device_id": "pixel-3f4a", "label": "Pixel"}
```

The device keeps `device_id` beside the key. Sending it back on a later pair
**refreshes that same row** — new label, new `last_seen`, the original
`paired_at` — instead of adding a second one.

Both extra fields are read only **after** the code has been found correct. A
refused claim answers exactly what it always did and writes nothing. And a
registry that cannot be written does not cost you the key: the claim answers
anyway and says so in the log.

## Choosing one, and forgetting one

The launch page's **Audio** control is the desk plus one radio per enrolled
device, labelled the way you named it (the id and the pairing date are on the
hover). It is drawn from the same `/admin/state` poll as everything else, so a
device paired on a phone in the next room appears at the desk within a few
seconds. With nothing paired the remote choice is shown held shut, with a link
to the pairing page.

- **Starting on a device nobody paired is refused** — `400`, *no paired device
  `<id>` — pair it first*. It is not a conversation that waits for ever for
  something that cannot answer.
- **Forget** is beside each device: a preview of what would go, then a
  confirming press. The list is copied beside itself first as
  `devices.toml.prev-<date>` — seconds appended if that name is taken, and an
  archive is never overwritten — and the weights file itself, the key, and every
  conversation are untouched.
- Forgetting the device a **running** conversation is speaking on is refused
  (`409`) and names it. Stop the conversation first.

## A device paired before the list existed

A device that paired before any of this has a stored name but no row. It needs
**one re-pair, about thirty seconds**: the pairing page sends the name it
already has as `device_id`, and because that name has the shape of an id and
nothing has taken it, the claim enrols a row **with that exact id**. So
`remote:<that name>` keeps working, the selector lists it, and nothing else has
to change.

(A device naming its own id is not a hole. It arrives holding a correct pairing
code and leaves holding the access key, which is strictly more than the right to
choose a name for itself.)

