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
  $$(".rail-btn, .tab").forEach((button) => button.classList.toggle("is-active", button.dataset.view === name));
  const section = $(`.view[data-view="${name}"]`);
  if (!mounted.has(name)) { VIEWS[name].mount(section); mounted.add(name); }
  VIEWS[name].show(arg);
  const search = $("#search");
  search.placeholder = HINTS[name] || "Rechercher un film, une série";
  search.value = VIEWS[name].query ? VIEWS[name].query() : "";
  $("#searchClear").hidden = !search.value;
  if (location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
  closeRail();
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

$$(".rail-btn, .tab").forEach((button) => button.addEventListener("click", () => go(button.dataset.view)));
const isPhone = () => window.matchMedia("(max-width: 900px)").matches;
function openRail() { $("#rail").hidden = false; $("#railScrim").hidden = false; }
function closeRail() { if (isPhone()) $("#rail").hidden = true; $("#railScrim").hidden = true; }
$("#menuBtn").addEventListener("click", openRail);
$("#railScrim").addEventListener("click", closeRail);
if (isPhone()) $("#rail").hidden = true;

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
$("#settingsBtn").addEventListener("click", openPanel);
$("#settingsClose").addEventListener("click", closePanel);
$("#panelScrim").addEventListener("click", closePanel);
$("#btnPassword").addEventListener("click", auth.changePassword);
$("#btn2fa").addEventListener("click", auth.twoFactor);
$("#btnSessions").addEventListener("click", auth.devices);
$("#btnAudit").addEventListener("click", auth.activity);
$("#logoutBtn").addEventListener("click", auth.logout);
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
  state.player = player || null;
  const active = Boolean(player && player.backend && player.backend !== "idle" && player.name);
  $("#nowPill").hidden = !active;
  if (active) $("#nowPill .now-text").textContent = `Sur la TV · ${player.name}`;
}
$("#nowPill").addEventListener("click", () => { location.href = "/remote/"; });

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
    if (isPhone() && !$("#rail").hidden) { closeRail(); return; }
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f") { event.preventDefault(); $("#search").focus(); return; }
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
  $("#who").textContent = me.username;
  const jellyfin = $("#jellyLink");
  jellyfin.hidden = !state.jellyfin;
  if (state.jellyfin) jellyfin.href = state.jellyfin;
  $$('.rail-btn[data-view="downloads"], .tab[data-view="downloads"]').forEach((button) => { button.hidden = !state.canTorrent; });
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
searchOverlay.init();
if (/\b(SmartTV|Tizen|Web0S|WebOS|BRAVIA|AFT[A-Z]|GoogleTV|HbbTV)\b/i.test(navigator.userAgent) && !opt.tv) {
  opt.tv = true;
  saveOpt();
  applyOpts();
}
auth.check().then((me) => { if (me) enter(me); });
