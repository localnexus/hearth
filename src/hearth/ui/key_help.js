// key_help.js — what the access key is, in plain words, on the two front doors.
//
// Spliced into the launch page and the first-run page, inside the token card,
// so the first thing a stranger is asked for is also explained. Static prose
// built with textContent — the page is served unauthed and must bake in no
// real key, so the example is a fixed, obviously patterned string.
window.HearthKeyHelp = (function () {
  const EXAMPLE = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
  function para(host, lead, text) {
    const p = document.createElement("p");
    const b = document.createElement("b");
    b.textContent = lead + " ";
    p.appendChild(b);
    p.appendChild(document.createTextNode(text));
    host.appendChild(p);
    return p;
  }
  function mount() {
    const host = document.getElementById("keyhelp");
    if (!host) return;
    host.replaceChildren();
    para(host, "What this key is.",
      "These pages can start and stop your companion and change its settings, " +
      "so they only answer to someone who has the key. Hearth made it just for " +
      "this install when you ran the setup command. It is not a password you " +
      "pick, and nobody else has a copy.");
    para(host, "Where to find it.",
      "The setup command printed it once. It is also saved in the file " +
      "config/serve-token inside your Hearth folder — open that file to see " +
      "it again.");
    const p = para(host, "What it looks like.",
      "One line of 64 letters and numbers, no spaces, like ");
    const code = document.createElement("code");
    code.textContent = EXAMPLE;
    p.appendChild(code);
    p.appendChild(document.createTextNode(
      " (an example — yours is different). Paste the whole line. This browser " +
      "remembers it after the first time."));
  }
  mount();
  return { mount: mount, EXAMPLE: EXAMPLE };
})();
