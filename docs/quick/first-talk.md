# Quick guide: your first talk

You have installed Hearth and run the first-run setup. This is the first conversation.

## Start Hearth and open the page

In a terminal window, inside your Hearth folder:

```bash
.venv/bin/python -m hearth.serve
```

Leave that window open. It is the running program. Now open this address in your browser:

```
http://127.0.0.1:65001/admin/launch
```

That address is your own computer. Nothing about it is on the internet.

## The access key

The page asks for an access key before it shows you anything. These pages can start and stop
your companion and change its settings, so they only answer to someone who has the key.

Hearth made the key for this install when you ran the setup. It printed it once. It is also
saved in a file inside your Hearth folder:

```
config/serve-token
```

Open that file to see the key again. It is one line of 64 letters and numbers with no
spaces, like `0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef`. That is an
example, and yours is different. Paste the whole line. Your browser remembers it after the
first time.

## Press Start

On a fresh install the page offers a short first run of three steps. It checks that your
model server is answering, writes down which model it found, starts the companion, and
confirms that it heard you. After that, this page is the front door, and **Start** is the
button.

Starting takes about 10 to 20 seconds while the voice engine warms up.

## Speak first

There is no greeting. The companion waits for you. Say hello out loud and wait a moment. The
first reply is slower than the rest, because the model server is still loading. After that a
reply comes about two to three seconds after you stop talking.

There is no button to press between turns. You talk the way you would on a phone call. If
you start talking while the companion is speaking, it stops and listens. That is meant to
happen.

Heard nothing at all? Your terminal app probably does not have microphone permission yet.
See [when it goes wrong](when-it-goes-wrong.md).

## Stopping

Press **Stop** on the page, or press Ctrl-C in the terminal window that is running Hearth.

## Where the conversation went

The talk is saved as a plain file on your computer, inside your Hearth folder:

```
characters/<character>/sessions/
```

`<character>` is the name of whoever you were talking to. The file is an ordinary list of
messages. You can open it, keep it, or delete it. Nothing was sent anywhere else.

**Go deeper:** [Installing Hearth, first launch](../installing.md) and
[The config layers](../the-config-layers.md).
