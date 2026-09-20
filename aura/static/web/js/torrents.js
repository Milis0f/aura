/* Aura web — find a file on the indexers the box knows, then download it through qBittorrent.
   The search runs on the box (/api/torrents/search): no CORS, and the indexer key never reaches the browser. */
import { $, h, I, api, state, opt, events, toast, dialog, busy, debounce, emptyState, bytes, speed, eta } from "./core.js";

const STATES = {
  downloading: ["Téléchargement", "dl"], forcedDL: ["Téléchargement", "dl"], metaDL: ["Lecture du lien", "dl"],
  stalledDL: ["Recherche de sources", ""], queuedDL: ["En file", ""], checkingDL: ["Vérification", ""],
  pausedDL: ["En pause", ""], stoppedDL: ["Arrêté", ""], uploading: ["Partage", "done"], forcedUP: ["Partage", "done"],
  stalledUP: ["Terminé", "done"], pausedUP: ["Terminé", "done"], stoppedUP: ["Terminé", "done"], queuedUP: ["Terminé", "done"],
  checkingUP: ["Vérification", ""], error: ["Erreur", ""], missingFiles: ["Fichiers manquants", ""], moving: ["Déplacement", ""],
};

const COLUMNS = [
  { key: "name", label: "Nom", dir: "asc" },
  { key: "size", label: "Taille", dir: "desc", num: true },
  { key: "seeders", label: "Sources", dir: "desc", num: true },
];

let list, magnet, category, query, results;
let timer = null;
let slow = false;
let inFlight = false;
let torrents = [];
let lastError = "";

let found = [];
let searchStatus = "idle"; // idle | loading | done | error
let searchError = "";
let warnings = [];
let sort = { key: "seeders", dir: "desc" };
let queued = new Set();
let searchRun = 0;
let searchAbort = null;

export function mount(section) {
  query = h("input", { type: "search", placeholder: "Chercher : distribution Linux, jeu libre, jeu de données…", autocomplete: "off", spellcheck: "false", enterkeyhint: "search" });
  query.addEventListener("input", debounce(() => runSearch(), 400));
  category = h("select", { "aria-label": "Destination" }, h("option", { value: "Films" }, "Films"), h("option", { value: "Series" }, "Séries"));
  const searchButton = h("button", { class: "btn", type: "submit" }, I("search"), "Chercher");
  results = h("div", { class: "results" });

  magnet = h("input", { placeholder: "Lien magnet ou adresse .torrent", autocomplete: "off", spellcheck: "false", enterkeyhint: "go" });
  const submit = h("button", { class: "btn primary", type: "submit" }, I("download"), "Lancer");
  list = h("div", { class: "torrents" });

  section.append(
    h("div", { class: "view-head" }, h("div", { class: "grow" },
      h("h1", { class: "view-title" }, "Téléchargements"),
      h("div", { class: "view-sub" }, "Cherche un fichier, il part dans qBittorrent et rejoint la bibliothèque tout seul."))),
    h("form", { class: "add-row", onsubmit: (event) => { event.preventDefault(); runSearch(); } }, query, category, searchButton),
    results,
    h("h2", { class: "section-title" }, "Ou colle un lien"),
    h("form", { class: "add-row", onsubmit: (event) => { event.preventDefault(); busy(submit, add); } }, magnet, submit),
    h("h2", { class: "section-title" }, "En cours"),
    list);
  renderSearch();
}

export function show() {
  category.value = opt.category;
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

async function runSearch() {
  const text = query.value.trim();
  if (searchAbort) searchAbort.abort();
  if (text.length < 2) {
    found = [];
    searchStatus = "idle";
    renderSearch();
    return;
  }
  searchAbort = new AbortController();
  const run = ++searchRun;
  searchStatus = "loading";
  renderSearch();
  try {
    const answer = await api(`/api/torrents/search?q=${encodeURIComponent(text)}&limit=40`, { signal: searchAbort.signal });
    if (run !== searchRun) return; // a later keystroke already won
    found = answer.results || [];
    warnings = answer.errors || [];
    searchStatus = "done";
  } catch (error) {
    if (run !== searchRun || error.name === "AbortError") return;
    searchError = error.message;
    searchStatus = "error";
  }
  renderSearch();
}

/** Rows with no value sink to the bottom whichever way the column is sorted. */
function sortFound() {
  const factor = sort.dir === "asc" ? 1 : -1;
  return [...found].sort((left, right) => {
    const a = sort.key === "name" ? left.name.toLowerCase() : left[sort.key];
    const b = sort.key === "name" ? right.name.toLowerCase() : right[sort.key];
    if (a == null && b == null) return 0;
    if (a == null) return 1;
    if (b == null) return -1;
    if (typeof a === "string") return a.localeCompare(b) * factor;
    return (a - b) * factor;
  });
}

function sortBy(column) {
  sort = sort.key === column.key ? { key: column.key, dir: sort.dir === "asc" ? "desc" : "asc" } : { key: column.key, dir: column.dir };
  renderSearch();
}

async function grab(row, button) {
  await busy(button, async () => {
    await api("/api/torrents/add", { method: "POST", form: { magnet: row.magnet || row.torrent_url, category: category.value } });
    queued.add(row.id);
    toast("Téléchargement lancé.", "ok", "download");
    renderSearch();
    tick();
  });
}

function resultRow(row) {
  const taken = queued.has(row.id);
  const action = taken
    ? h("span", { class: "res-none" }, "Ajouté")
    : h("button", { class: "btn small", onclick: (event) => grab(row, event.currentTarget) }, I("download"), "Télécharger");
  return h("tr", {},
    h("td", {},
      h("div", { class: "res-name", title: row.name }, row.name),
      h("span", { class: "res-meta" },
        row.indexer || "",
        row.indexer && row.details_url ? " · " : "",
        row.details_url ? h("a", { href: row.details_url, target: "_blank", rel: "noreferrer noopener" }, "Fiche") : null)),
    h("td", { class: "num" }, row.size ? bytes(row.size) : "—"),
    h("td", { class: `num ${row.seeders ? "res-seed" : "res-none"}` }, row.seeders == null ? "—" : String(row.seeders)),
    h("td", { class: "num" }, action));
}

function renderSearch() {
  if (!results) return;
  if (searchStatus === "idle") {
    results.replaceChildren(emptyState("search", "Cherche un fichier",
      "Deux lettres suffisent. Aura interroge le catalogue public de l'Internet Archive, et ton indexeur auto-hébergé s'il est configuré (AURA_INDEXER_URL)."));
    return;
  }
  if (searchStatus === "loading") {
    results.replaceChildren(...Array.from({ length: 4 }, () => h("div", { class: "sk sk-row" })));
    return;
  }
  if (searchStatus === "error") {
    results.replaceChildren(emptyState("info", "Recherche impossible", searchError, {
      actions: [h("button", { class: "btn", onclick: runSearch }, I("refresh"), "Réessayer")],
    }));
    return;
  }
  if (!found.length) {
    results.replaceChildren(emptyState("search", "Aucun résultat",
      "Essaie un autre mot. Le catalogue public couvre les films du domaine public, les logiciels libres et les jeux de données ; pour le reste, configure ton propre indexeur."));
    return;
  }

  const head = h("tr", {}, ...COLUMNS.map((column) => h("th", {
    class: `${column.num ? "num" : ""} ${sort.key === column.key ? "is-sorted" : ""}`.trim(),
    "aria-sort": sort.key === column.key ? (sort.dir === "asc" ? "ascending" : "descending") : "none",
  }, h("button", { type: "button", onclick: () => sortBy(column) }, column.label,
    h("span", { class: "sort-caret" }, sort.key === column.key ? (sort.dir === "asc" ? "▲" : "▼") : "↕")))),
  h("th", { class: "num" }, h("button", { type: "button", disabled: true }, "Action")));

  results.replaceChildren(
    h("table", { class: "res-table" }, h("thead", {}, head), h("tbody", {}, sortFound().map(resultRow))),
    ...warnings.map((warning) => h("p", { class: "res-warn" }, `${warning.source} : ${warning.message}`)));
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
  const down = torrents.reduce((sum, t) => sum + (t.dl || 0), 0);
  const up = torrents.reduce((sum, t) => sum + (t.up || 0), 0);
  $("#throughput").hidden = !(down || up);
  $("#tpDown").textContent = speed(down);
  $("#tpUp").textContent = speed(up);
  document.documentElement.style.setProperty("--glow", Math.min(down / (12 * 1048576), 1).toFixed(3));
  $("#pulse").classList.toggle("busy", down > 0);
  const active = torrents.filter((t) => t.dl > 0).length;
  $("#dlBadge").hidden = !active;
  $("#dlBadge").textContent = String(active);
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
    list.replaceChildren(emptyState("magnet", "Aucun téléchargement en cours", "Cherche un fichier ci-dessus, ou colle un lien : il part dans Films ou Séries et apparaît dans la bibliothèque une fois terminé."));
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
  await api("/api/torrents/add", { method: "POST", form: { magnet: link, category: category.value } });
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
