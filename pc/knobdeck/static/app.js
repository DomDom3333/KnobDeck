"use strict";
/* KnobDeck settings window */

const TOKEN = window.KD_TOKEN;
const SLOTS = 8;
const COLORS = ["#1E88E5", "#3949AB", "#8E24AA", "#D81B60", "#E53935", "#FB8C00", "#FDD835",
                "#43A047", "#00897B", "#00ACC1", "#6D4C41", "#546E7A", "#212121"];
const TYPES = [["action", "Action"], ["screen", "Screen"], ["dial", "Dial"], ["stats", "Stats"], ["people", "Voice"], ["folder", "Folder"], ["back", "Back"]];
const TYPE_HELP = {
  action: "Runs an action on the PC when you press the knob.",
  screen: "Opens a full screen drawn by a plugin (menus, timers, graphs, ...): it gets every turn and press of the knob. Hold the knob to go back. Needs deck firmware 2.0.",
  dial: "Opens a full-screen dial: turning the knob changes the value, pressing toggles (e.g. mute), holding exits.",
  stats: "Opens a full-screen view with up to three live rings. Press or hold to go back.",
  folder: "Opens another page on the deck. Hold the knob (or use a Back tile) to return.",
  back: "Goes back to the previous page.",
  people: "Opens a screen with the people in your voice chat around the edge; a green ring shows who's speaking. Turn to pick someone, press to act on them, hold to go back.",
};
const BADGE = {folder: "▸", dial: "◔", stats: "≡", back: "↩", people: "☺", screen: "▣"};

const S = {rev: "", msgTime: 0, sync: {}, platform: "windows", autostartLabel: "", inputStatus: "", cfg: null, saved: "", cat: null, syms: [], fw: {}, dev: null, page: 0, slot: null, tab: "tile", pstatus: {},
           autostart: false, autostartSupported: false, dataDir: ""};

/* ---------------------------------------------------------------- helpers */

const $ = (sel, el = document) => el.querySelector(sel);

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k === "style") el.style.cssText = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k in el && typeof v !== "string") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

async function api(path, body) {
  const opt = {headers: {"X-KD-Token": TOKEN}};
  if (body !== undefined) {
    opt.method = "POST";
    if (body instanceof Blob) opt.body = body;
    else { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
  }
  const r = await fetch(path, opt);
  const data = await r.json().catch(() => ({ok: false, error: `HTTP ${r.status}`}));
  if (!r.ok && !data.error) data.error = `HTTP ${r.status}`;
  return data;
}

let toastTimer;
function toast(text, ms = 2200) {
  const t = $("#toast");
  t.textContent = text;
  t.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), ms);
}

const rid = (p = "t") => p + Math.random().toString(16).slice(2, 8);
const page = () => S.cfg.pages[S.page];
const tile = () => (S.slot === null ? null : page().tiles[S.slot]);
const entry = id => S.cat.entries.find(e => e.id === id);
const entriesOf = kind => S.cat.entries.filter(e => e.kind === kind);

function iconEl(icon, label, size) {
  const box = h("div", {class: "ico", style: `width:${size}px;height:${size}px`});
  icon = icon || "";
  if (icon.startsWith("sym:")) {
    box.append(h("img", {src: `/static/sym/${icon.slice(4)}.png`, alt: "", draggable: false}));
  } else if (icon.startsWith("img:")) {
    box.append(h("img", {src: `/api/icon/${icon.slice(4)}.png?k=${encodeURIComponent(TOKEN)}`, alt: "", draggable: false}));
  } else {
    const txt = icon.startsWith("txt:") ? icon.slice(4) : (label || "?").slice(0, 2);
    box.append(h("span", {class: "txt", text: txt, style: `font-size:${Math.round(size * (txt.length > 2 ? 0.3 : 0.42))}px`}));
  }
  return box;
}

function dirty() { return JSON.stringify(S.cfg) !== S.saved; }

function markDirty() {
  const d = dirty();
  $("#save").disabled = !d;
  $("#revert").disabled = !d;
  $("#dirty").textContent = d ? "Unsaved changes" : "";
}

function changed(rerenderEditor = false) {
  markDirty();
  renderDeck();
  renderPages();
  if (rerenderEditor) renderEditor();
}

/* ---------------------------------------------------------------- deck preview */

function renderDeck() {
  const deck = $("#deck");
  deck.querySelectorAll(".slot").forEach(e => e.remove());
  const p = page();
  const R = 158, C = 220;
  p.tiles.forEach((t, i) => {
    const a = (-90 + i * 45) * Math.PI / 180;
    const el = h("div", {
      class: "slot " + (t ? "tile" : "empty") + (S.slot === i ? " sel" : ""),
      style: `left:${C + R * Math.cos(a)}px;top:${C + R * Math.sin(a)}px;` + (t ? `background:${t.color}` : ""),
      title: t ? `${t.label || "(no label)"}` : "Empty slot - click to add a tile",
      onclick: () => { S.slot = i; setTab("tile"); renderDeck(); },
    });
    if (t) {
      el.append(iconEl(t.icon, t.label, 50));
      if (BADGE[t.type]) el.append(h("span", {class: "badge", text: BADGE[t.type]}));
      el.draggable = true;
      el.addEventListener("dragstart", e => {
        e.dataTransfer.setData("text/kd-slot", JSON.stringify({page: S.page, slot: i}));
        e.dataTransfer.effectAllowed = "move";
      });
    } else {
      el.textContent = "+";
    }
    el.addEventListener("dragover", e => { e.preventDefault(); el.classList.add("dragover"); });
    el.addEventListener("dragleave", () => el.classList.remove("dragover"));
    el.addEventListener("drop", e => {
      e.preventDefault();
      el.classList.remove("dragover");
      const src = JSON.parse(e.dataTransfer.getData("text/kd-slot") || "null");
      if (!src || src.page !== S.page || src.slot === i) return;
      const tiles = page().tiles;
      [tiles[src.slot], tiles[i]] = [tiles[i], tiles[src.slot]];
      S.slot = i;
      changed(true);
    });
    deck.append(el);
  });
  // centre: like the deck shows the selected tile
  const sel = tile() || p.tiles.find(Boolean);
  $("#c-title").textContent = p.title;
  const ci = $("#c-icon");
  ci.replaceChildren(sel ? iconEl(sel.icon, sel.label, 64) : "");
  $("#c-label").textContent = sel ? sel.label : "Empty page";
}

/* ---------------------------------------------------------------- pages */

function renderPages() {
  const ul = $("#pagelist");
  ul.replaceChildren();
  S.cfg.pages.forEach((p, i) => {
    const n = p.tiles.filter(Boolean).length;
    const li = h("li", {class: i === S.page ? "sel" : "", onclick: () => { S.page = i; S.slot = null; renderAll(); }},
      h("span", {text: (i === 0 ? "⌂ " : "") + (p.title || p.id)}), h("span", {class: "n", text: `${n}/8`}));
    li.addEventListener("dragover", e => { if (i !== S.page) { e.preventDefault(); li.classList.add("drop"); } });
    li.addEventListener("dragleave", () => li.classList.remove("drop"));
    li.addEventListener("drop", e => {
      e.preventDefault();
      li.classList.remove("drop");
      const src = JSON.parse(e.dataTransfer.getData("text/kd-slot") || "null");
      if (!src) return;
      const dst = S.cfg.pages[i].tiles;
      const free = dst.indexOf(null);
      if (free < 0) return toast("That page is full");
      dst[free] = S.cfg.pages[src.page].tiles[src.slot];
      S.cfg.pages[src.page].tiles[src.slot] = null;
      S.slot = null;
      changed(true);
      toast(`Moved to "${S.cfg.pages[i].title}"`);
    });
    ul.append(li);
  });
  const pt = $("#pagetitle");
  if (document.activeElement !== pt) pt.value = page().title;
  $("#delpage").disabled = S.page === 0;
  $("#delpage").title = S.page === 0 ? "The first page is the home page and can't be deleted" : "";
}

function addPage(title = "New page", withBack = true) {
  const p = {id: rid("p"), title, tiles: Array(SLOTS).fill(null)};
  if (withBack) p.tiles[0] = {id: rid(), type: "back", label: "Back", icon: "sym:left", color: "#455A64", action: "", params: {}};
  S.cfg.pages.push(p);
  return p;
}

/* ---------------------------------------------------------------- editor */

function setTab(name) {
  S.tab = name;
  document.querySelectorAll(".tabs button").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  ["tile", "device", "plugins"].forEach(t => $(`#tab-${t}`).classList.toggle("hidden", t !== name));
  renderEditor();
}

function renderEditor() {
  if (S.tab === "tile") renderTileTab();
  else if (S.tab === "device") renderDeviceTab();
  else renderPluginsTab();
}

function newTile(type) {
  const base = {id: rid(), type, label: "", icon: "", color: COLORS[0], action: "", params: {}};
  if (type === "action") Object.assign(base, {label: "New action", icon: "sym:ok"});
  if (type === "dial") Object.assign(base, {label: "Volume", icon: "sym:volume", color: "#43A047", action: "audio.volume"});
  if (type === "stats") Object.assign(base, {label: "PC Stats", icon: "sym:charge", color: "#FB8C00", action: "stats.system"});
  if (type === "back") Object.assign(base, {label: "Back", icon: "sym:left", color: "#455A64"});
  if (type === "people") Object.assign(base, {label: "Discord", icon: "sym:call", color: "#5865F2", action: "discord.voice"});
  if (type === "screen") Object.assign(base, {label: "Timer", icon: "sym:hourglass", color: "#00897B", action: "screens.timer"});
  if (type === "folder") {
    const p = addPage("Folder");
    Object.assign(base, {label: "Folder", icon: "sym:folder", color: "#8E24AA", target: p.id});
  }
  const e = entry(base.action);
  if (e) base.params = defaults(e);
  return base;
}

function defaults(e) {
  const out = {};
  (e.params || []).forEach(p => { out[p.key] = p.default; });
  return out;
}

function renderTileTab() {
  const box = $("#tab-tile");
  box.replaceChildren();
  if (S.slot === null) {
    box.append(h("h3", {text: page().title}), h("p", {class: "hint", text: "Click a slot on the deck to edit it, or an empty one (+) to add a tile."}));
    return;
  }
  const t = tile();
  if (!t) {
    box.append(h("h3", {text: `Empty slot ${S.slot + 1}`}), h("p", {class: "hint", text: "What should this tile do?"}));
    const q = h("div", {class: "quick"});
    for (const [type, name] of TYPES) {
      q.append(h("button", {onclick: () => { page().tiles[S.slot] = newTile(type); changed(true); }},
        h("b", {text: name}), h("span", {text: TYPE_HELP[type].split(":")[0].split(".")[0]})));
    }
    box.append(q);
    return;
  }

  box.append(h("h3", {text: t.label || "Tile"}));

  // type
  box.append(h("label", {text: "Type"}));
  const seg = h("div", {class: "seg"});
  for (const [type, name] of TYPES) {
    seg.append(h("button", {class: t.type === type ? "on" : "", text: name, onclick: () => {
      if (t.type === type) return;
      const fresh = newTile(type);
      t.type = type;
      t.action = fresh.action;
      t.params = fresh.params;
      if (type === "folder") t.target = fresh.target; else delete t.target;
      if (!t.label || ["New action", "Back", "Volume", "PC Stats", "Folder", "Discord", "Timer"].includes(t.label)) t.label = fresh.label;
      t.icon = fresh.icon;
      changed(true);
    }}));
  }
  box.append(seg, h("div", {class: "help", text: TYPE_HELP[t.type]}));

  // label
  box.append(h("label", {}, "Label (shown in the middle of the deck)",
    h("input", {value: t.label, maxlength: 31, oninput: e => { t.label = e.target.value; changed(); }})));

  // what it does
  if (["action", "dial", "stats", "people", "screen"].includes(t.type)) {
    box.append(actionPicker(t, t.type, t.type === "people" ? "Source" : t.type === "screen" ? "Screen" : "Action", false));
  }
  if (t.type === "folder") {
    const sel = h("select", {onchange: e => {
      if (e.target.value === "__new") { const p = addPage(t.label || "Folder"); t.target = p.id; }
      else t.target = e.target.value;
      changed(true);
    }});
    S.cfg.pages.forEach((p, i) => { if (i !== S.page) sel.append(h("option", {value: p.id, text: p.title, selected: p.id === t.target})); });
    sel.append(h("option", {value: "__new", text: "+ New page"}));
    if (!S.cfg.pages.some(p => p.id === t.target)) sel.prepend(h("option", {value: "", text: "(choose a page)", selected: true}));
    box.append(h("label", {}, "Opens page", sel));
    const target = S.cfg.pages.findIndex(p => p.id === t.target);
    if (target >= 0) box.append(h("button", {class: "ghost small", text: "Edit that page →", style: "margin-top:8px",
      onclick: () => { S.page = target; S.slot = null; renderAll(); }}));
  }

  // icon + colour
  box.append(iconEditor(t));
  box.append(h("label", {text: "Colour"}), colorEditor(t));

  // long press
  if (t.type === "action") {
    const det = h("details", {open: !!(t.long && t.long.action)}, h("summary", {text: "Long press (optional, home page only)"}));
    const holder = t.long || {action: "", params: {}};
    det.append(actionPicker(holder, "action", "Action when held", true, () => { t.long = holder; }));
    box.append(det);
  }

  // test + delete
  const result = h("div", {class: "result"});
  const testBtn = ["action", "dial", "stats", "people", "screen"].includes(t.type) && t.action
    ? h("button", {text: "Test on PC", onclick: async () => {
        result.className = "result";
        result.textContent = "Running...";
        const r = await api("/api/test", {tile: t});
        result.className = "result " + (r.ok ? "ok" : "err");
        result.textContent = r.message || (r.ok ? "Done" : r.error || "Failed");
      }}) : null;
  box.append(h("div", {class: "row", style: "margin-top:20px"}, testBtn, h("div", {class: "spacer"}),
    h("button", {class: "danger", text: "Delete tile", onclick: () => { page().tiles[S.slot] = null; changed(true); }})), result);
}

function actionPicker(holder, kind, labelText, allowNone, attach) {
  const wrap = h("div");
  const sel = h("select", {onchange: e => {
    if (attach) attach();
    const old = entry(holder.action);
    const e2 = entry(e.target.value);
    holder.action = e.target.value;
    holder.params = e2 ? defaults(e2) : {};
    // follow the action's name/icon unless the user has customised them
    if (holder === tile() && e2) {
      if (!holder.label || holder.label === "New action" || (old && holder.label === old.name)) holder.label = e2.name;
      if (e2.icon && (!holder.icon || holder.icon === "sym:ok" || (old && holder.icon === old.icon))) holder.icon = e2.icon;
    }
    changed(true);
  }});
  if (allowNone || !holder.action) sel.append(h("option", {value: "", text: allowNone ? "(nothing)" : "(choose)"}));
  const groups = {};
  entriesOf(kind).forEach(e => { (groups[e.group] = groups[e.group] || []).push(e); });
  Object.keys(groups).sort().forEach(g => {
    const og = h("optgroup", {label: g});
    groups[g].forEach(e => og.append(h("option", {value: e.id, text: e.name, selected: e.id === holder.action})));
    sel.append(og);
  });
  if (holder.action && !entry(holder.action)) {
    sel.prepend(h("option", {value: holder.action, text: `${holder.action} (plugin missing)`, selected: true}));
  }
  wrap.append(h("label", {}, labelText, sel));
  const e = entry(holder.action);
  if (e) {
    if (e.help) wrap.append(h("div", {class: "help", text: e.help}));
    const params = holder.params || {};
    e.params.forEach(p => wrap.append(paramField(p, params, () => { holder.params = params; if (attach) attach(); })));
  }
  return wrap;
}

function paramField(p, values, attach) {
  // defaults are not written into the config until the user changes a field (keeps "Unsaved changes" honest)
  const cur = p.key in values ? values[p.key] : p.default;
  const set = v => { values[p.key] = v; if (attach) attach(); changed(); };
  let input;
  switch (p.type) {
    case "bool":
      return h("label", {class: "check"}, h("input", {type: "checkbox", checked: !!cur, onchange: e => set(e.target.checked)}), p.label);
    case "number":
      input = h("input", {type: "number", value: cur, oninput: e => set(e.target.value === "" ? p.default : Number(e.target.value))});
      break;
    case "select": {
      input = h("select", {onchange: e => set(e.target.value)});
      (p.options || []).forEach(o => {
        const [v, l] = Array.isArray(o) ? o : [o, o];
        input.append(h("option", {value: v, text: l, selected: v === cur}));
      });
      break;
    }
    case "page":
      input = h("select", {onchange: e => set(e.target.value)});
      S.cfg.pages.forEach(pg => input.append(h("option", {value: pg.id, text: pg.title, selected: pg.id === cur})));
      break;
    case "textarea":
      input = h("textarea", {placeholder: p.placeholder, oninput: e => set(e.target.value)});
      input.value = cur ?? "";
      break;
    case "ircode": {
      const area = h("textarea", {placeholder: p.placeholder || "Learn it from your remote, or paste timings / a Pronto hex code",
        oninput: e => set(e.target.value), style: "min-height:60px;font-family:monospace;font-size:11px"});
      area.value = cur ?? "";
      const msg = h("span", {class: "help", style: "margin:0"});
      const learn = h("button", {class: "small", text: "Learn from remote", onclick: async () => {
        learn.disabled = true;
        msg.textContent = "Point the remote at the deck and press the button (10 s)...";
        const r = await api("/api/ir_learn", {timeout: 10});
        learn.disabled = false;
        if (r.ok) { area.value = r.raw.join(", "); set(area.value); msg.textContent = `Got it (${r.raw.length} timings)`; }
        else msg.textContent = r.error || "Failed";
      }});
      const test = h("button", {class: "small", text: "Send", onclick: async () => {
        const r = await api("/api/ir_send", {code: area.value});
        msg.textContent = r.ok ? "Sent" : (r.error || "Failed");
      }});
      input = h("div", {}, area, h("div", {class: "row", style: "margin-top:5px"}, learn, test, msg));
      break;
    }
    case "secret":
      input = h("input", {type: "password", value: cur ?? "", placeholder: p.placeholder, oninput: e => set(e.target.value)});
      break;
    case "hotkey": {
      const field = h("input", {value: cur ?? "", placeholder: p.placeholder, oninput: e => set(e.target.value)});
      const rec = h("button", {class: "small kbd-rec", text: "Record", onclick: () => recordHotkey(field, rec, v => set(v))});
      input = h("div", {class: "row", style: "margin-top:5px"}, field, rec);
      break;
    }
    default:
      input = h("input", {value: cur ?? "", placeholder: p.placeholder, oninput: e => set(e.target.value)});
  }
  const lab = h("label", {}, p.label, input);
  return p.help ? h("div", {}, lab, h("div", {class: "help", text: p.help})) : lab;
}

const KEYNAMES = {Control: "ctrl", Shift: "shift", Alt: "alt", Meta: "win", " ": "space", ArrowUp: "up", ArrowDown: "down",
  ArrowLeft: "left", ArrowRight: "right", Escape: "esc", Enter: "enter", Tab: "tab", Backspace: "backspace",
  Delete: "delete", Insert: "insert", Home: "home", End: "end", PageUp: "pageup", PageDown: "pagedown",
  PrintScreen: "printscreen", "+": "plus", "-": "minus", ",": "comma", ".": "period"};

function recordHotkey(field, btn, done) {
  btn.textContent = "Press keys...";
  field.value = "";
  field.focus();
  const onKey = e => {
    e.preventDefault();
    e.stopPropagation();
    const mods = [];
    if (e.ctrlKey) mods.push("ctrl");
    if (e.shiftKey) mods.push("shift");
    if (e.altKey) mods.push("alt");
    if (e.metaKey) mods.push("win");
    let k = KEYNAMES[e.key] ?? (e.key.length === 1 ? e.key.toLowerCase() : e.key.toLowerCase());
    if (["ctrl", "shift", "alt", "win"].includes(k)) { field.value = mods.join("+") + "+..."; return; }
    if (e.code && e.code.startsWith("Key")) k = e.code.slice(3).toLowerCase();        // ignore keyboard layout
    if (e.code && e.code.startsWith("Digit")) k = e.code.slice(5);
    const combo = [...mods, k].join("+");
    field.value = combo;
    done(combo);
    window.removeEventListener("keydown", onKey, true);
    btn.textContent = "Record";
  };
  window.addEventListener("keydown", onKey, true);
}

function iconEditor(t) {
  const wrap = h("div");
  const kind = t.icon.startsWith("img:") ? "img" : t.icon.startsWith("txt:") ? "txt" : "sym";
  const prev = h("div", {class: "iconprev", style: `background:${t.color}`}, iconEl(t.icon, t.label, 34));
  const seg = h("div", {class: "seg", style: "flex:1"});
  const body = h("div");
  const show = k => {
    seg.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.k === k));
    body.replaceChildren();
    if (k === "sym") {
      const grid = h("div", {class: "symgrid"});
      S.syms.forEach(s => grid.append(h("button", {class: t.icon === "sym:" + s ? "on" : "", title: s,
        onclick: () => { t.icon = "sym:" + s; changed(true); }}, h("img", {src: `/static/sym/${s}.png`, alt: s}))));
      body.append(grid);
    } else if (k === "img") {
      const file = h("input", {type: "file", accept: "image/*,.ico", class: "hidden", onchange: e => upload(e.target.files[0])});
      const zone = h("div", {class: "drop-zone", text: "Drop an image here or click to choose (PNG, JPG, SVG, ICO). Transparent PNGs look best.",
        onclick: () => file.click()});
      zone.addEventListener("dragover", e => { e.preventDefault(); zone.classList.add("over"); });
      zone.addEventListener("dragleave", () => zone.classList.remove("over"));
      zone.addEventListener("drop", e => { e.preventDefault(); zone.classList.remove("over"); upload(e.dataTransfer.files[0]); });
      async function upload(f) {
        if (!f) return;
        zone.textContent = "Converting...";
        const r = await api("/api/icon", f);
        if (r.hash) { t.icon = "img:" + r.hash; changed(true); }
        else zone.textContent = "Could not read that image: " + (r.error || "");
      }
      body.append(zone, file);
    } else {
      body.append(h("input", {value: t.icon.startsWith("txt:") ? t.icon.slice(4) : "", maxlength: 5, placeholder: "Up to 5 letters, e.g. OBS",
        oninput: e => { t.icon = "txt:" + e.target.value; changed(); prev.replaceChildren(iconEl(t.icon, t.label, 34)); }}));
    }
  };
  [["sym", "Symbol"], ["img", "Image"], ["txt", "Text"]].forEach(([k, n]) =>
    seg.append(h("button", {"data-k": k, text: n, onclick: () => show(k)})));
  wrap.append(h("label", {text: "Icon"}), h("div", {class: "iconrow"}, prev, seg), body);
  show(kind);
  return wrap;
}

function colorEditor(t) {
  const row = h("div", {class: "swatches"});
  COLORS.forEach(c => row.append(h("span", {class: "swatch" + (t.color.toUpperCase() === c ? " on" : ""), style: `background:${c}`,
    title: c, onclick: () => { t.color = c; changed(true); }})));
  row.append(h("input", {type: "color", value: t.color, oninput: e => { t.color = e.target.value.toUpperCase(); changed(); },
    onchange: () => renderEditor()}));
  return row;
}

/* ---------------------------------------------------------------- device + plugins tabs */

function renderDeviceTab() {
  const box = $("#tab-device");
  const d = S.cfg.device;
  const dev = S.dev || {};
  const info = dev.info || {};
  const slider = (key, label, min, max, unit) => {
    const val = h("span", {text: `${d[key]}${unit}`});
    return h("label", {}, h("span", {}, label + ": ", val),
      h("input", {type: "range", min, max, value: d[key], oninput: e => { d[key] = Number(e.target.value); val.textContent = `${d[key]}${unit}`; markDirty(); }}));
  };
  const idle = h("select", {onchange: e => { d.idle = Number(e.target.value); markDirty(); }});
  [[0, "Never"], [30, "30 seconds"], [60, "1 minute"], [120, "2 minutes"], [300, "5 minutes"], [600, "10 minutes"], [1800, "30 minutes"]]
    .forEach(([v, n]) => idle.append(h("option", {value: v, text: n, selected: Number(d.idle) === v})));
  const port = h("select", {onchange: e => { d.port = e.target.value; markDirty(); }});
  port.append(h("option", {value: "auto", text: "Automatic", selected: d.port === "auto"}));
  (dev.ports || []).forEach(p => port.append(h("option", {value: p.device, selected: p.device === d.port,
    text: `${p.device} - ${p.description}${p.deck ? "  (KnobDeck)" : ""}`})));

  box.replaceChildren(...[
    h("h3", {text: "Deck"}),
    h("div", {class: "help"}, dev.connected
      ? `Connected on ${dev.port} · firmware ${info.fw || "?"} · ${Math.round((info.free || 0) / 1024)} KB free for icons`
      : (dev.error || "Not connected. Plug the deck in with a data USB cable.")),
    S.platform === "linux" && S.inputStatus ? h("div", {class: "help", text: `Hotkeys & media keys: ${S.inputStatus}`}) : null,
    slider("brightness", "Brightness", 10, 100, "%"),
    h("label", {}, "Dim the screen after", idle),
    slider("dim", "Dimmed brightness", 0, 50, "%"),
    h("label", {class: "check", style: "margin-top:14px"},
      h("input", {type: "checkbox", checked: !!d.invert, onchange: e => { d.invert = e.target.checked; markDirty(); }}),
      "Reverse knob direction"),
    h("label", {class: "check"},
      h("input", {type: "checkbox", checked: !!d.click, onchange: e => { d.click = e.target.checked; markDirty(); }}),
      "Click sound when turning the knob"),
    h("label", {}, "USB port", port),
    h("div", {class: "row"}, h("button", {class: "small", text: "Reconnect", onclick: async () => { await api("/api/reconnect", {}); toast("Reconnecting..."); }})),
    firmwareBox(dev, info),
    syncBox(dev),
    h("details", {}, h("summary", {text: "Advanced"}),
      h("label", {}, "IR pin (GPIO, default 4)", h("input", {type: "number", min: 0, max: 21, value: d.ir_pin ?? 4,
        oninput: e => { d.ir_pin = Number(e.target.value); markDirty(); }})),
      h("div", {class: "help", text: "On the ESP32-C3-LCDkit the IR LED and receiver share GPIO 4; jumper J7 on the main board selects which one is connected (RX to learn, TX to send)."})),
    h("h3", {text: "PC app"}),
    S.autostartSupported ? autostartField() : null,
    h("div", {class: "help", text: `Settings, icons and your own plugins are stored in ${S.dataDir}`}),
    h("div", {class: "help", text: "Brightness and knob settings are sent to the deck when you press Save."}),
  ].filter(Boolean));
}

function firmwareBox(dev, info) {
  const fw = S.fw || {};
  const st = fw.status || {};
  const box = h("div", {class: "card", style: "margin-top:14px"}, h("h3", {text: "Firmware", style: "margin-top:6px"}));
  const have = info.fw || "?";
  if (!dev.connected) box.append(h("div", {class: "help", text: `The app includes firmware ${fw.bundled || "?"}. Connect the deck to update it.`}));
  else if (fw.update) box.append(h("div", {class: "help"}, h("b", {text: `Update available: ${have} → ${fw.bundled}. `}),
    "New screens, popups, LED, sounds, IR and accents (äöü...) need it. Your tiles and pictures stay on the deck."));
  else box.append(h("div", {class: "help", text: `Deck runs ${have} - up to date (app includes ${fw.bundled || "?"}).`}));
  if (st.state === "running") {
    box.append(h("progress", {max: 100, value: st.progress || 0, style: "width:100%"}),
      h("div", {class: "help", text: `${st.message || ""} ${st.progress ? st.progress + "%" : ""} - don't unplug the deck`}));
  } else {
    if (st.state === "done") box.append(h("div", {class: "result ok", text: st.message}));
    if (st.state === "error") box.append(h("div", {class: "result err", text: st.message}));
    if (!fw.esptool) box.append(h("div", {class: "help", text: "Updating needs esptool: run install.bat again."}));
    box.append(h("button", {class: fw.update ? "" : "small", text: fw.update ? `Update to ${fw.bundled}` : "Re-install firmware",
      disabled: !dev.connected || !fw.esptool || !fw.bundled, onclick: async () => {
        if (!fw.update && !confirm(`Write firmware ${fw.bundled} to the deck again?`)) return;
        const r = await api("/api/firmware", {});
        if (!r.ok) return toast(r.error || "Could not start the update", 4000);
        S.fw.status = {state: "running", progress: 0, message: "Starting..."};
        renderDeviceTab();
      }}));
  }
  return box;
}

function autostartField() {
  const sel = h("select", {onchange: async e => {
    const r = await api("/api/autostart", {mode: e.target.value});
    if (!r.ok) return toast(r.error || "Failed", 4000);
    S.autostartMode = r.mode;
    S.autostartNote = r.note || "";
    toast(r.mode === "off" ? "Autostart off" : "Saved");
    renderDeviceTab();
  }});
  (S.autostartModes || []).forEach(([v, l]) => sel.append(h("option", {value: v, text: l, selected: v === S.autostartMode})));
  return h("div", {}, h("label", {}, "Start KnobDeck automatically", sel),
    S.autostartNote ? h("div", {class: "help", text: S.autostartNote}) : null);
}

function syncBox(dev) {
  const sy = S.sync || {};
  const box = h("div", {class: "card", style: "margin-top:14px"}, h("h3", {text: "Settings on the deck", style: "margin-top:6px"}));
  if (!dev.connected) {
    box.append(h("div", {class: "help", text: "Your pages, actions and plugin settings are also stored on the deck, so a new PC picks them up when you plug it in."}));
    return box;
  }
  if (!sy.supported) {
    box.append(h("div", {class: "help", text: "Needs deck firmware 2.1 (update above): then your settings are stored on the deck and travel with it."}));
    return box;
  }
  const when = sy.last ? new Date(sy.last * 1000).toLocaleString() : "never";
  box.append(h("div", {class: "help", text: sy.in_sync ? `In sync with the deck (last synced ${when}). Passwords stay on this PC.`
    : sy.deck_has ? "The deck has different settings - they sync when you save or replug the deck." : "Nothing stored on the deck yet - save once."}));
  const act = (action, question) => async () => {
    if (question && !confirm(question)) return;
    const r = await api("/api/sync", {action});
    toast(r.ok ? "Done" : (r.error || "Failed"), 3000);
  };
  box.append(h("div", {class: "row", style: "gap:6px;flex-wrap:wrap"},
    h("button", {class: "small", text: "Load from deck", onclick: act("pull", "Replace the settings on this PC with the ones stored on the deck? (A backup is kept.)")}),
    h("button", {class: "small", text: "Store on deck", onclick: act("push")}),
    sy.backup ? h("button", {class: "small ghost", text: "Undo last load", title: sy.backup,
      onclick: act("undo", `Go back to the settings from ${sy.backup}?`)}) : null));
  return box;
}

function renderPluginsTab() {
  const box = $("#tab-plugins");
  box.replaceChildren(h("h3", {text: "Plugin settings"}));
  const settings = S.cat.settings || {};
  Object.entries(settings).forEach(([id, spec]) => {
    const vals = S.cfg.plugins[id] || {};
    const card = h("div", {class: "card", "data-plugin": id}, h("h3", {text: spec.name, style: "margin-top:10px"}));
    if (spec.help) card.append(h("div", {class: "help", text: spec.help}));
    card.append(h("div", {class: "pstatus", text: S.pstatus[id] || ""}));
    spec.params.forEach(p => card.append(paramField(p, vals, () => { S.cfg.plugins[id] = vals; })));
    box.append(card);
  });
  box.append(h("h3", {text: "Available actions"}));
  const byPlugin = {};
  S.cat.entries.forEach(e => { (byPlugin[e.plugin] = byPlugin[e.plugin] || []).push(e); });
  Object.keys(byPlugin).sort().forEach(pl => {
    box.append(h("div", {class: "help"}, h("b", {text: pl + ": "}), byPlugin[pl].map(e => e.name).join(", ")));
  });
  box.append(h("p", {class: "help", text: `Add your own: put a .py file into ${S.dataDir}${S.platform === "windows" ? "\\" : "/"}plugins and restart KnobDeck. ` +
    "See PLUGINS.md for the format."}));
  (S.cat.errors || []).forEach(err => box.append(h("div", {class: "err-box", text: err})));
}

/* ---------------------------------------------------------------- status + save */

function renderStatus() {
  const pill = $("#devstatus");
  const dev = S.dev || {};
  pill.classList.toggle("on", !!dev.connected);
  $("#devtext").textContent = dev.connected ? `Deck connected · ${dev.port}` : (dev.error ? "Deck: no access to the port" : "Deck not connected");
}

function renderAll() {
  renderPages();
  renderDeck();
  renderEditor();
  renderStatus();
  markDirty();
}

async function load() {
  const st = await api("/api/state");
  S.cfg = st.config;
  S.saved = JSON.stringify(st.config);
  S.cat = st.catalog;
  S.syms = st.symbols;
  S.fw = st.firmware || {};
  S.rev = st.config_rev;
  S.msgTime = (st.config_msg || {}).time || 0;
  S.sync = st.sync || {};
  S.dev = st.device;
  S.autostart = st.autostart;
  S.autostartSupported = st.autostart_supported;
  S.autostartLabel = st.autostart_label || "Start automatically";
  S.autostartMode = st.autostart_mode;
  S.autostartModes = st.autostart_modes;
  S.autostartNote = st.autostart_note;
  S.platform = st.platform;
  S.inputStatus = st.input_status;
  S.dataDir = st.data_dir;
  S.pstatus = st.plugin_status || {};
  renderAll();
}

async function poll() {
  const st = await api("/api/state").catch(() => null);
  if (!st || !st.device) {
    $("#devtext").textContent = "KnobDeck app is not running";
    $("#devstatus").classList.remove("on");
    return;
  }
  const was = JSON.stringify([S.dev, S.fw, S.sync]);
  S.sync = st.sync || {};
  if (st.config_rev !== S.rev) {
    // settings changed outside this window (loaded from the deck, undo ...)
    S.rev = st.config_rev;
    const msg = (st.config_msg || {}).time > S.msgTime ? st.config_msg.text : "Settings changed";
    S.msgTime = (st.config_msg || {}).time || S.msgTime;
    if (!dirty()) {
      S.cfg = st.config;
      S.saved = JSON.stringify(st.config);
      if (S.page >= S.cfg.pages.length) S.page = 0;
      S.slot = null;
      renderAll();
      toast(msg, 4000);
    } else {
      toast(`${msg} - you have unsaved changes here; saving will replace them`, 6000);
    }
  }
  S.dev = st.device;
  S.fw = st.firmware || {};
  S.syms = st.symbols;
  S.autostart = st.autostart;
  S.pstatus = st.plugin_status || {};
  document.querySelectorAll(".card[data-plugin] .pstatus").forEach(el => {
    el.textContent = S.pstatus[el.parentElement.dataset.plugin] || "";
  });
  renderStatus();
  if (S.tab === "device" && was !== JSON.stringify([S.dev, S.fw, S.sync]) && !["SELECT", "INPUT"].includes(document.activeElement.tagName)) renderDeviceTab();
}

async function save() {
  const r = await api("/api/config", {config: S.cfg});
  if (!r.ok) return toast("Could not save: " + (r.error || "unknown error"), 4000);
  S.cfg = r.config;
  S.saved = JSON.stringify(r.config);
  S.rev = (r.config.rev || {}).id || "";
  if (S.page >= S.cfg.pages.length) S.page = 0;
  renderAll();
  toast(S.dev && S.dev.connected ? "Saved — sent to the deck" : "Saved — the deck gets it when it's connected");
}

function wire() {
  $("#save").onclick = save;
  $("#revert").onclick = () => {
    S.cfg = JSON.parse(S.saved);
    if (S.page >= S.cfg.pages.length) S.page = 0;
    S.slot = null;
    renderAll();
  };
  $("#addpage").onclick = () => { addPage("New page"); S.page = S.cfg.pages.length - 1; S.slot = null; changed(true); };
  $("#pagetitle").oninput = e => { page().title = e.target.value; changed(); };
  $("#showpage").onclick = async () => {
    const r = await api("/api/preview", {page: page().id});
    toast(r.ok ? "Showing it on the deck (save first if it's new)" : "Deck not connected");
  };
  $("#delpage").onclick = () => {
    if (S.page === 0) return;
    const p = page();
    if (!confirm(`Delete the page "${p.title}" and its tiles?`)) return;
    S.cfg.pages.forEach(pg => pg.tiles.forEach(t => { if (t && t.type === "folder" && t.target === p.id) t.target = ""; }));
    S.cfg.pages.splice(S.page, 1);
    S.page = 0;
    S.slot = null;
    changed(true);
  };
  document.querySelectorAll(".tabs button").forEach(b => { b.onclick = () => setTab(b.dataset.tab); });
  window.addEventListener("keydown", e => {
    if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); if (dirty()) save(); }
  });
  window.addEventListener("beforeunload", e => { if (S.cfg && dirty()) { e.preventDefault(); e.returnValue = ""; } });
}

wire();
load().then(() => setInterval(poll, 1000)).catch(err => {
  document.body.replaceChildren(h("p", {style: "padding:40px", text: "Could not load settings: " + err}));
});
