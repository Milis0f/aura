/* Aura web — find a title on the indexers the box knows, then download it through qBittorrent.
   The search runs on the box (/api/torrents/search): no CORS, and the indexer key never reaches the browser. */
import { $, h, I, api, state, opt, events, toast, dialog, busy, debounce, emptyState, bytes, speed, eta } from "./core.js";
import { resultGrid } from "./torrent-grid.js";
import { closeCard } from "./torrent-sheet.js";
import { forget as forgetVolumes } from "./torrent-target.js";

const STATES = {
  downloading: ["Téléchargement", "dl"], forcedDL: ["Téléchargement", "dl"], metaDL: ["Lecture du lien", "dl"],
  stalledDL: ["Recherche de sources", ""], queuedDL: ["En file", ""], checkingDL: ["Vérification", ""],
  pausedDL: ["En pause", ""], stoppedDL: ["Arrêté", ""], uploading: ["Partage", "done"], forcedUP: ["Partage", "done"],
  stalledUP: ["Terminé", "done"], pausedUP: ["Terminé", "done"], stoppedUP: ["Terminé", "done"], queuedUP: ["Terminé", "done"],
  checkingUP: ["Vérification", ""], error: ["Erreur", ""], missingFiles: ["Fichiers manquants", ""], moving: ["Déplacement", ""],
};

const PAGE = 40;

let list, magnet, category, query, grid;
let timer = null;
let slow = false;
let inFlight = false;
let torrents = [];
let lastError = "";
let searchRun = 0;
let searchAbort = null;

export function mount(section) {
  query = h("input", { type: "search", placeholder: "Chercher : film du domaine public, distribution Linux, jeu libre…", autocomplete: "off", spellcheck: "false", enterkeyhint: "search" });
  query.addEventListener("input", debounce(() => runSearch(), 400));
  category = h("select", { "aria-label": "Dossier par défaut" }, h("option", { value: "Films" }, "Films"), h("option", { value: "Series" }, "Séries"));
  const searchButton = h("button", { class: "btn", type: "submit" }, I("search"), "Chercher");
  grid = resultGrid({ onSearch: runSearch, onStarted: () => { closeCard(); tick(); } });

  magnet = h("input", { placeholder: "Lien magnet ou adresse .torrent", autocomplete: "off", spellcheck: "false", enterkeyhint: "go" });
  const submit = h("button", { class: "btn primary", type: "submit" }, I("download"), "Lancer");
  list = h("div", { class: "torrents" });

  section.append(
    h("div", { class: "view-head" }, h("div", { class: "grow" },
      h("h1", { class: "view-title" }, "Téléchargements"),
      h("div", { class: "view-sub" }, "Cherche un titre, il part dans qBittorrent et rejoint la bibliothèque tout seul."))),
    h("form", { class: "add-row", onsubmit: (event) => { event.preventDefault(); runSearch(); } }, query, category, searchButton),
    grid.el,
    h("h2", { class: "section-title" }, "Ou colle un lien"),
    h("form", { class: "add-row", onsubmit: (event) => { event.preventDefault(); busy(submit, add); } }, magnet, submit),
    h("h2", { class: "section-title" }, "En cours"),
    list);
  grid.render();
}

export function show() {
  category.value = opt.category;
  forgetVolumes(); // a drive may have been plugged or removed since the last visit
  render();
  if (!slow) tick();
}

export function start() { if (!timer) restart(); }

function restart(interval = Number(opt.refresh) || 3000, immediate = true) {
  clearInterval(timer);
  if (!state.canTorrent) return;
  if (immediate) tick();
  timer = setInterval(tick, interval);
}
events.on("downloads-options", () => { slow = false; restart(); });

/* ---------------------------------------------------------------- search */

async function runSearch(limit = PAGE) {
  const text = query.value.trim();
  if (searchAbort) searchAbort.abort();
  if (text.length < 2) {
    grid.set({ status: "idle", cards: [], warnings: [], truncated: false, limit: PAGE });
    return;
  }
  searchAbort = new AbortController();
  const run = ++searchRun;
  grid.set({ status: "loading", limit });
  try {
    const answer = await api(`/api/torrents/search?q=${encodeURIComponent(text)}&limit=${limit}`, { signal: searchAbort.signal });
    if (run !== searchRun) return; // a later keystroke already won
    grid.set({
      status: "done",
      cards: answer.cards || [],
      warnings: answer.errors || [],
      truncated: (answer.results || []).length >= limit && limit < 200,
    });
  } catch (error) {
    if (run !== searchRun || error.name === "AbortError") return;
    grid.set({ status: "error", error: error.message });
  }
}

/* ---------------------------------------------------------------- downloads */

async function tick() {
  if (!state.canTorrent || !state.csrf || document.hidden || inFlight) return;
  inFlight = true;
  try {
    torrents = await api("/api/torrents");
    lastError = "";
    if (slow) { slow = false; restart(); }
  } catch (error) {
    torrents = [];
    lastError = error.message;
    // qBittorrent is down: back off to one probe every 20 s instead of hammering it every 3 s.
    if (!slow) { slow = true; restart(20000, false); }
  } finally {
    inFlight = false;
  }
  // The bars that used to carry these readings are gone. Throughput still drives the ground's glow,
  // and the count rides the one button that is always on screen.
  const down = torrents.reduce((sum, t) => sum + (t.dl || 0), 0);
  document.documentElement.style.setProperty("--glow", Math.min(down / (12 * 1048576), 1).toFixed(3));
  const active = torrents.filter((t) => t.dl > 0).length;
  const button = $("#openSearch");
  if (button) {
    button.dataset.count = active ? String(active) : "";
    button.classList.toggle("is-busy-dl", active > 0);
  }
  if (state.view === "downloads" && list) render();
}

function render() {
  if (lastError) {
    list.replaceChildren(emptyState("download", "qBittorrent ne répond pas", lastError, {
      steps: ["Vérifie que qBittorrent tourne et que son interface Web est activée (sur le boîtier : systemctl status aura-qbittorrent).", "Dans qBittorrent › Options › Web UI : coche « Contourner l'authentification pour les clients sur localhost », ou renseigne AURA_QB_USER et AURA_QB_PASS dans /etc/aura.env."],
    }));
    return;
  }
  if (!torrents.length) {
    list.replaceChildren(emptyState("magnet", "Aucun téléchargement en cours", "Cherche un titre ci-dessus, ou colle un lien : il part dans Films ou Séries et apparaît dans la bibliothèque une fois terminé."));
    return;
  }
  list.replaceChildren(...torrents.map(card));
}

function card(t) {
  const [label, cls] = STATES[t.state] || [t.state, ""];
  const live = t.dl > 0;
  const buttons = h("span", { class: "tor-btns" });
  const button = (title, icon, fn, extra = "") => buttons.append(h("button", { class: `icon-btn ${extra}`, title, "aria-label": title, onclick: fn }, I(icon)));
  if (/paused|stopped/i.test(t.state)) button("Reprendre", "play", () => act(t.hash, "resume"));
  else button("Mettre en pause", "pause", () => act(t.hash, "pause"));
  button("Retirer", "trash", () => remove(t), "danger");
  return h("div", { class: "tor" },
    h("div", { class: "tor-top" }, h("div", { class: "tor-name" }, t.name), h("span", { class: `tor-state ${cls}` }, label)),
    h("div", { class: "bar" }, h("div", { class: `bar-fill${live ? " live" : ""}`, style: `width:${t.progress}%` })),
    h("div", { class: "tor-foot" },
      h("span", {}, h("b", {}, `${String(t.progress).replace(".", ",")} %`), ` · ${bytes(t.done)} / ${bytes(t.size)}`),
      live ? h("span", {}, `${speed(t.dl)} Mo/s`) : null,
      live ? h("span", {}, eta(t.eta)) : null,
      h("span", {}, `${t.seeds} sources`),
      buttons));
}

async function add() {
  const link = magnet.value.trim();
  if (!link) return;
  await api("/api/torrents/add", { method: "POST", form: { magnet: link, category: category.value, volume: "" } });
  magnet.value = "";
  toast("Téléchargement lancé.", "ok", "download");
  tick();
}

async function act(hash, action, deleteFiles = false) {
  try {
    await api("/api/torrents/action", { method: "POST", form: { hashes: hash, do: action, delete_files: deleteFiles ? "true" : "false" } });
    tick();
  } catch (error) { toast(error.message, "err"); }
}

async function remove(t) {
  const files = h("input", { type: "checkbox", class: "sw" });
  const body = h("label", { class: "opt" }, h("span", {}, "Supprimer aussi les fichiers"), files);
  if (!await dialog({ title: "Retirer le téléchargement", text: t.name, body, ok: "Retirer", danger: true })) return;
  act(t.hash, "delete", files.checked);
}
