// launch_devices.js — the launch page's audio route control: where this
// conversation will listen and speak, fixed before it starts.
//
// One radio for the desk and one for every device that has PAIRED. The list
// arrives on the page's own state poll, so pairing a phone in the next room
// adds a choice here and forgetting one takes it away, without this file
// knowing anything about either door.
//
// Two rules the drawing obeys, both about not moving under somebody's hand:
// the checked radio survives a redraw, and a redraw happens only when the ids
// or the labels actually changed. routeChoices() and routeSignature() are pure
// and are cut out of the SERVED page and run under node by test_voice_page.py.
//
// Forget runs the same preview-then-confirm the Models card runs — the
// server's own words, including the name of the copy it leaves behind, and a
// second press before anything happens. The device a live conversation is
// using is held shut here as a courtesy; the server says the same with a 409.
//
// Spliced into the launch page's own <script> block, so the scope above is the
// host: $, el, show, api, remembered and remember from ui/admin_shell.js, the
// polled `devices` list, and refresh(). chosenRoute() is what the Start body's
// `route` is read from, so it stays callable from the page's Start path, and
// the page draws the control once at load.
//
// Nothing here is drawn from markup: every node is built, so a device's own
// label never meets innerHTML.
// ── the audio route control ─────────────────────────────────────────────────
// One radio for the desk, one for every device that has PAIRED. The list comes
// from the /admin/state poll, so pairing a phone in the next room adds a choice
// here without touching this page, and forgetting one takes it away again.
//
// Two rules the drawing obeys, both about not moving under somebody's hand:
// the checked radio survives a redraw, and a redraw happens only when the ids
// or the labels actually changed.
const ROUTE_KEY = "hearth_route_choice";

// Pure, and cut out by test_voice_page.py. → {rows, checked}: what the control
// should hold, and which radio should be on, given the enrolled devices, what
// this browser remembers, what is checked right now, and the route a live
// conversation is using (that device's forget link is held — the server says
// the same with a 409; this is only the courtesy of not offering it).
function routeChoices(devices, stored, checkedNow, liveRoute) {
  const list = Array.isArray(devices) ? devices : [];
  const rows = [{value: "desk", label: "the desk", title: "", forget: ""}];
  if (!list.length) {
    rows.push({value: "", label: "a paired device — pair one first",
               title: "", forget: "", disabled: true});
  }
  for (const device of list) {
    const day = String(device.paired_at || "").slice(0, 10);
    rows.push({
      value: "remote:" + device.id,
      label: device.label || device.id,
      title: device.id + (day ? " · paired " + day : ""),
      forget: liveRoute === "remote:" + device.id ? "held" : device.id,
    });
  }
  const want = stored === "desk" ? "desk" : (stored ? "remote:" + stored : "");
  const usable = (v) => !!v && rows.some((r) => r.value === v && !r.disabled);
  return {rows: rows,
          checked: usable(checkedNow) ? checkedNow : usable(want) ? want : "desk"};
}

// What a redraw would change. Equal signatures mean there is nothing to draw.
function routeSignature(devices, liveRoute) {
  return (devices || []).map((d) => d.id + "\u0000" + (d.label || "")).join("|") +
         "\u0001" + (liveRoute || "");
}

let routeDrawn = null;

function checkedRoute() {
  const on = document.querySelector('input[name="pick-route"]:checked');
  return on ? on.value : "";
}

function chosenRoute() {
  const value = checkedRoute() || "desk";
  remember(ROUTE_KEY, value === "desk" ? "desk" : value.replace(/^remote:/, ""));
  return value;
}

function noteRoute() {
  const value = checkedRoute();
  show("routenote", !!value && value !== "desk");
}

function drawRouteControl(list, liveRoute) {
  const signature = routeSignature(list, liveRoute);
  if (signature === routeDrawn) return;
  routeDrawn = signature;
  const plan = routeChoices(list, remembered(ROUTE_KEY), checkedRoute(), liveRoute);
  const box = $("routechoices");
  while (box.firstChild) box.removeChild(box.firstChild);
  for (const row of plan.rows) {
    const label = el("label");
    if (row.title) label.title = row.title;
    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = "pick-route";
    radio.value = row.value;
    radio.disabled = !!row.disabled;
    radio.checked = row.value === plan.checked;
    radio.addEventListener("change", noteRoute);
    label.appendChild(radio);
    label.appendChild(document.createTextNode(" " + row.label));
    box.appendChild(label);
    if (row.disabled) {
      const pair = el("a", "note", "pair one");
      pair.href = "/admin/pair/ui";
      box.appendChild(pair);
    } else if (row.forget === "held") {
      box.appendChild(el("span", "note", "(in use)"));
    } else if (row.forget) {
      const link = el("button", "linky", "forget");
      link.addEventListener("click", () => offerForget(row.forget, row.label));
      box.appendChild(link);
    }
  }
  noteRoute();
}

// Forget: the same preview-then-confirm the Models card runs. The preview is
// the server's own words, including the name of the copy it leaves behind.
function offerForget(id, label) {
  const pane = $("routeforget");
  const clear = () => { while (pane.firstChild) pane.removeChild(pane.firstChild); };
  clear();
  pane.appendChild(el("div", "note", "reading…"));
  api("/admin/devices/forget", {json: {id: id}}).then((r) => {
    clear();
    const d = r.data || {};
    if (r.status !== 200 || !d.ok) {
      pane.appendChild(el("div", "err", d.error || ("refused (" + r.status + ")")));
      return;
    }
    pane.appendChild(el("div", "note", d.confirm));
    if (d.archives) pane.appendChild(el("div", "state", "copied to " + d.archives));
    const go = el("button", "primary", "Confirm");
    const no = el("button", "", "Cancel");
    const row = el("div", "row");
    row.appendChild(go); row.appendChild(no);
    pane.appendChild(row);
    no.addEventListener("click", clear);
    go.addEventListener("click", () => {
      go.disabled = true; no.disabled = true;
      api("/admin/devices/forget", {json: {id: id, yes: true}}).then((done) => {
        clear();
        const dd = done.data || {};
        if (done.status !== 200 || !dd.ok) {
          pane.appendChild(el("div", "err", dd.error || "refused"));
          return;
        }
        pane.appendChild(el("div", "note", label + " is off the list"));
        routeDrawn = null;   // the list changed under us — draw it again
        refresh();
      }).catch((e) => {
        clear();
        pane.appendChild(el("div", "err",
          e.message === "401" ? "key refused" : "failed: " + e.message));
      });
    });
  }).catch((e) => {
    clear();
    pane.appendChild(el("div", "err",
      e.message === "401" ? "key refused" : "failed: " + e.message));
  });
}
