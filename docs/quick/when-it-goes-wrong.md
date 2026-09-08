# Quick guide: when it goes wrong

Three problems account for most bad first days. Each one looks alarming and each one has a
short fix.

## 1. You talk and nothing happens

**What you see.** Hearth starts. The page says the companion is ready. You speak, and nothing
comes back. No error appears anywhere. It simply sits there.

**What it means.** macOS gives microphone permission to the app that owns the terminal
window, not to Python. If that permission was never granted, Hearth hears silence forever,
and silence is not an error, so nothing is reported.

**What to do.** Open System Settings, then Privacy and Security, then Microphone. Turn on
your terminal app in that list. Quit the terminal app completely and open it again, then
start Hearth from that same app. From now on, always start Hearth from the app you allowed.

## 2. The model server is not running

**What you see.** The install prints a note saying nothing is answering yet. Or the check
command says the server cannot be reached:

```bash
./start.sh --check
```

**What it means.** Hearth does not ship a language model and does not run one. It talks to a
server you start yourself. If that server is not up, Hearth has nobody to think with.

**What to do.** Open a second terminal window and start your model server there. Leave that
window open. It has to keep running the whole time you are talking. Then check that it
answers:

```bash
curl -s http://127.0.0.1:8080/v1/models
```

If it answers on a different address, tell Hearth where to look with the `LM_BASE_URL`
setting. If it answers with a 401, it wants a key, which goes in `LM_API_TOKEN`.

## 3. The address is already taken

**What you see.** Hearth exits shortly after you start it, printing a line that says it could
not take the address `127.0.0.1:65001` and asking whether a copy is already running.

**What it means.** Two copies cannot use the same address. One is usually still running in
another terminal window you forgot about.

**What to do.** Look for that other window and press Ctrl-C in it. If you cannot find it, ask
your Mac who has the address:

```bash
lsof -i :65001
```

Then stop that program and start Hearth again.

## Anything else

Two more that are easy to mistake for something worse. If the reply arrives but the voice
stutters, the machine is working too hard for the setting it is on. If the companion goes
quiet right after it starts thinking, the model is thinking out loud instead of answering,
and thinking has to be turned off for that model.

**Go deeper:** [Debugging notes](../debugging/README.md) and
[Installing Hearth, quick troubleshooting](../installing.md).
