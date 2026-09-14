/* Aura web — downloads through qBittorrent: add a link, follow progress, pause, resume, remove. */
import { $, h, I, api, state, opt, events, toast, dialog, busy, emptyState, bytes, speed, eta } from "./core.js";

const STATES = {
  downloading: ["Téléchargement", "dl"], forcedDL: ["Téléchargement", "dl"], metaDL: ["Lecture du lien", "dl"],
  stalledDL: ["Recherche de sources", ""], queuedDL: ["En file", ""], checkingDL: ["Vérification", ""],
  pausedDL: ["En pause", ""], stoppedDL: ["Arrêté", ""], uploading: ["Partage", "done"], forcedUP: ["Partage", "done"],
  stalledUP: ["Terminé", "done"], pausedUP: ["Terminé", "done"], stoppedUP: ["Terminé", "done"], queuedUP: ["Terminé", "done"],
  checkingUP: ["Vérification", ""], error: ["Erreur", ""], missingFiles: ["Fichiers manquants", ""], moving: ["Déplacement", ""],
};

let list, magnet, category;
let timer = null;
let slow = false;
let torrents = [];
let lastError = "";

export function mount(section) {
  magnet = h("input", { placeholder: "Lien magnet ou adresse .torrent", autocomplete: "off", spellcheck: "false", enterkeyhint: "go" });
  category = h("select", { "aria-label": "Destination" }, h("option", { value: "Films" }, "Films"), h("option", { value: "Series" }, "Séries"));
  const submit = h("button", { class: "btn primary", type: "submit" }, I("download"), "Lancer");
  list = h("div", { class: "torrents" });
  section.append(
    h("div", { class: "view-head" }, h("div", { class: "grow" },
      h("h1", { class: "view-title" }, "Téléchargements"),
      h("div", { class: "view-sub" }, "Les films terminés rejoignent la bibliothèque tout seuls."))),
    h("form", { class: "add-row", onsubmit: (e) => { e.preventDefault(); busy(submit, add); } }, magnet, category, submit),
    h("div", { style: "height:18px" }),
    list);
}

export function show() {
  category.value = opt.category;
  render();
  tick();
}

export function start() { restart(); }

function restart(interval = Number(opt.refresh) || 3000) {
  clearInterval(timer);
  if (!state.canTorrent) return;
  tick();
  timer = setInterval(tick, interval);
}
events.on("downloads-options", () => { slow = false; restart(); });

async function tick() {
  if (!state.canTorrent || !state.csrf || document.hidden) return;
  try {
    torrents = await api("/api/torrents");
    lastError = "";
    if (slow) { slow = false; restart(); }
  } catch (error) {
    torrents = [];
    lastError = error.message;
    if (!slow) { slow = true; restart(20000); }
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
      steps: ["Vérifie que le service tourne : systemctl status qbittorrent", "Dans qBittorrent, coche « Contourner l'authentification pour localhost »."],
    }));
    return;
  }
  if (!torrents.length) {
    list.replaceChildren(emptyState("magnet", "Aucun téléchargement en cours", "Colle un lien ci-dessus : il part dans Films ou Séries et apparaît dans la bibliothèque une fois terminé."));
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
