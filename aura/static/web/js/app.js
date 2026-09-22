/* Aura web — boot, navigation, settings panel, live events from the box. */
import { $, $$, h, api, state, opt, saveOpt, applyOpts, busy, events, toast, hydrate, spatialMove, topLayer, debounce } from "./core.js";
import * as searchOverlay from "./search-overlay.js";
import * as auth from "./auth.js";
import * as viewer from "./viewer.js";
import * as library from "./library.js";
import * as drives from "./drives.js";
import * as files from "./files.js";
import * as downloads from "./torrents.js";
import * as system from "./system.js";

const VIEWS = { library, drives, files, downloads, system };
const HINTS = { files: "Rechercher dans cet emplacement" };
const mounted = new Set();
let serverSettings = {};

/* ---------------------------------------------------------------- navigation */
function go(name, arg) {
  if (!VIEWS[name] || (name === "downloads" && !state.canTorrent)) name = "library";
  state.view = name;
  $$(".view").forEach((section) => section.classList.toggle("is-active", section.dataset.view === name));
  // No navigation chrome to highlight any more: the body carries the current screen so CSS can react,
  // which is how the home gets its own bare layout.
  document.body.dataset.view = name;
  const section = $(`.view[data-view="${name}"]`);
  if (!mounted.has(name)) { VIEWS[name].mount(section); mounted.add(name); }
  VIEWS[name].show(arg);
  if (location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
}
events.on("navigate", ({ view, arg }) => go(view, arg));
window.addEventListener("hashchange", () => {
  const name = location.hash.slice(1);
  if (!$("#app").hidden && VIEWS[name] && name !== state.view) go(name);
});

const runSearch = debounce((q) => {
  const view = VIEWS[state.view];
  if (view && view.search) view.search(q);
  else if (q) { go("library"); library.search(q); }
}, 280);
$("#search").addEventListener("input", (e) => { $("#searchClear").hidden = !e.target.value; runSearch(e.target.value.trim()); });
$("#searchClear").addEventListener("click", () => {
  $("#search").value = "";
  $("#searchClear").hidden = true;
  runSearch("");
  $("#search").focus();
});

/** Every place the field can take you. The palette renders from this, so adding a screen here is
    all it takes for it to become reachable - there is no menu to remember to update. */
export const SCREENS = [
  { view: "library", label: "Bibliothèque", icon: "film" },
  { view: "drives", label: "Disques", icon: "drive" },
  { view: "files", label: "Fichiers", icon: "folder" },
  { view: "downloads", label: "Téléchargements", icon: "download", needs: "canTorrent" },
  { view: "system", label: "Système", icon: "gauge" },
];

export const ACTIONS = [
  { label: "Réglages", icon: "sliders", run: () => openPanel() },
  { label: "Télécommande TV", icon: "remote", run: () => { location.href = "/remote/"; } },
  { label: "Se déconnecter", icon: "logout", run: () => auth.logout() },
];

export function reachable() {
  return [
    ...SCREENS.filter((screen) => !screen.needs || state[screen.needs]).map((screen) => ({ ...screen, kind: "écran" })),
    ...ACTIONS.map((action) => ({ ...action, kind: "action" })),
  ];
}

/* ---------------------------------------------------------------- settings */
async function syncServerSettings() {
  try { serverSettings = (await api("/api/settings")).settings || {}; } catch { return; }
  if (!$("#settings").hidden) buildSettings();
}

function bind(selector, key, isCheck = false, after = null) {
  const el = $(selector);
  if (isCheck) el.checked = Boolean(opt[key]); else el.value = String(opt[key]);
  el.onchange = () => {
    opt[key] = isCheck ? el.checked : (el.value === "" || Number.isNaN(Number(el.value)) ? el.value : Number(el.value));
    saveOpt();
    applyOpts();
    if (after) after();
  };
}

function serverToggle(selector, key) {
  const el = $(selector);
  el.checked = serverSettings[key] !== "0";
  el.onchange = async () => {
    try {
      await api("/api/settings", { method: "PUT", json: { values: { [key]: el.checked ? "1" : "0" } } });
      serverSettings[key] = el.checked ? "1" : "0";
      toast("Réglage enregistré.", "ok");
    } catch (error) {
      el.checked = !el.checked;
      toast(error.message, "err");
    }
  };
}

/** Posters and summaries all come from TMDB; without a key the library is a wall of typography. The
    field only ever sends a new key - the server never gives the stored one back. */
function tmdbField() {
  const input = $("#optTmdb");
  const save = $("#tmdbSave");
  const note = $("#tmdbState");
  const stored = serverSettings.tmdb_api_key_set === "1";
  input.placeholder = stored ? "Clé enregistrée — colle une nouvelle clé pour la remplacer" : "Clé API TMDB (v3)";
  note.textContent = stored
    ? "Affiches et résumés activés. Laisse vide pour garder la clé actuelle."
    : "Sans clé, Aura affiche un carton à la place des affiches.";
  save.onclick = () => busy(save, async () => {
    const value = input.value.trim();
    if (!value) { toast("Colle une clé, ou laisse le champ tel quel.", "err"); return; }
    await api("/api/settings", { method: "PUT", json: { values: { tmdb_api_key: value } } });
    serverSettings.tmdb_api_key_set = "1";
    input.value = "";
    tmdbField();
    toast("Clé enregistrée. Les affiches arrivent au prochain scan.", "ok", "check");
  });
}

function buildSettings() {
  bind("#optDensity", "density");
  bind("#optAurora", "aurora", true);
  bind("#optTv", "tv", true);
  bind("#optPlayTarget", "playTarget");
  bind("#optHidden", "hidden", true, () => events.emit("files-options"));
  bind("#optConfirm", "confirm", true);
  bind("#optSort", "sort", false, () => events.emit("files-options"));
  bind("#optCategory", "category");
  bind("#optRefresh", "refresh", false, () => events.emit("downloads-options"));
  serverToggle("#optAutoplay", "autoplay_next");
  serverToggle("#optAutomount", "automount");
  tmdbField();
  $("#btn2faLabel").textContent = state.has2fa ? "Désactiver la double authentification" : "Activer la double authentification";
  $("#panelVersion").textContent = `Aura ${state.version} · connecté en ${state.user}`;
}

function openPanel() { buildSettings(); $("#settings").hidden = false; $("#panelScrim").hidden = false; }
function closePanel() { $("#settings").hidden = true; $("#panelScrim").hidden = true; }
$("#settingsClose").addEventListener("click", closePanel);
$("#panelScrim").addEventListener("click", closePanel);
$("#btnPassword").addEventListener("click", auth.changePassword);
$("#btn2fa").addEventListener("click", auth.twoFactor);
$("#btnSessions").addEventListener("click", auth.devices);
$("#btnAudit").addEventListener("click", auth.activity);
events.on("account-changed", () => { if (!$("#settings").hidden) buildSettings(); });

/* ---------------------------------------------------------------- live events */
let socket = null;
let retry = null;

function connectSocket() {
  if (socket && socket.readyState <= 1) return;
  socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/ws?role=remote`);
  socket.onmessage = (event) => {
    let message;
    try { message = JSON.parse(event.data); } catch { return; }
    handleEvent(message);
  };
  socket.onclose = () => {
    socket = null;
    clearTimeout(retry);
    if (!$("#app").hidden) retry = setTimeout(connectSocket, 3000);
  };
  socket.onerror = () => { if (socket) socket.close(); };
}
setInterval(() => { if (socket && socket.readyState === 1) socket.send(JSON.stringify({ type: "ping" })); }, 25000);

function updateNow(player) {
  // What is playing is shown by the home's resume strip now, where it belongs: on the poster.
  state.player = player || null;
}

function handleEvent(message) {
  const drive = message.drive || {};
  switch (message.type) {
    case "hello": updateNow(message.player); break;
    case "player": updateNow(message.state); break;
    case "drive_added": toast(`Disque « ${drive.label} » branché. Recherche des films…`, "ok", "usb"); break;
    case "drive_removed": toast(`« ${drive.label} » débranché.`, "", "eject"); break;
    case "library_scan":
      if (message.state === "done" && message.new_items) {
        toast(`${message.new_items} ${message.new_items > 1 ? "nouveaux titres" : "nouveau titre"} sur « ${drive.label} ».`, "ok", "film");
      } else if (message.state === "done" && message.films !== undefined) {
        toast(`« ${drive.label} » analysé : ${message.films} films, ${message.episodes} épisodes.`, "ok", "scan");
      } else if (message.state === "error") {
        toast(`Analyse de « ${drive.label} » impossible : ${message.error}`, "err");
      }
      break;
    case "settings_changed": syncServerSettings(); break;
    default: break;
  }
  Object.values(VIEWS).forEach((view) => { if (view.onEvent) view.onEvent(message); });
}

/* ---------------------------------------------------------------- keyboard */
document.addEventListener("keydown", (event) => {
  if (!$("#modal").hidden) return;
  if (viewer.handleKey(event)) { event.preventDefault(); return; }
  const tag = document.activeElement ? document.activeElement.tagName : "";
  const typing = tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA";
  if (event.key === "Escape") {
    if (!$("#detail").hidden) { library.closeDetail(); return; }
    if (!$("#settings").hidden) { closePanel(); return; }
    if (searchOverlay.isOpen()) { searchOverlay.close(); return; }
  }
  // One shortcut for the one field: it searches the library and it reaches every screen.
  if ((event.ctrlKey || event.metaKey) && ["k", "f"].includes(event.key.toLowerCase())) {
    event.preventDefault();
    searchOverlay.open();
    return;
  }
  const directions = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down" };
  if (directions[event.key] && !typing && (opt.tv || topLayer() !== $("#app"))) {
    event.preventDefault();
    spatialMove(directions[event.key]);
    return;
  }
  const view = VIEWS[state.view];
  if (!typing && view && view.onKey && topLayer() === $("#app")) view.onKey(event);
});

/* ---------------------------------------------------------------- boot */
function enter(me) {
  Object.assign(state, {
    csrf: me.csrf, user: me.username, canWrite: Boolean(me.can_write), canTorrent: Boolean(me.can_torrent),
    has2fa: Boolean(me.has_2fa), zone: me.zone, version: me.version || "", jellyfin: me.jellyfin || "",
  });
  $("#gate").hidden = true;
  $("#app").hidden = false;
  // The class is dropped once the sequence has run, so a later navigation does not replay it.
  document.body.classList.add("arriving");
  setTimeout(() => document.body.classList.remove("arriving"), 2600);
  syncServerSettings();
  connectSocket();
  downloads.start();
  const initial = location.hash.slice(1);
  go(VIEWS[initial] ? initial : "library");
}
events.on("signed-in", enter);
events.on("unauthorized", () => { state.csrf = ""; auth.check(); });

applyOpts();
hydrate();
searchOverlay.init({ reachable, go });
$("#openSearch").addEventListener("click", () => searchOverlay.open());

// Scrolled past the stage, the field takes the top instead of staying pinned over the banners.
// Each view scrolls itself, so the listener rides the container rather than the window.
$("#content").addEventListener("scroll", (event) => {
  document.body.classList.toggle("scrolled", event.target.scrollTop > 160);
}, { capture: true, passive: true });
if (/\b(SmartTV|Tizen|Web0S|WebOS|BRAVIA|AFT[A-Z]|GoogleTV|HbbTV)\b/i.test(navigator.userAgent) && !opt.tv) {
  opt.tv = true;
  saveOpt();
  applyOpts();
}
auth.check().then((me) => { if (me) enter(me); });
