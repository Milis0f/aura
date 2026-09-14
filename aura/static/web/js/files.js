/* Aura web — files: the NAS file manager (browse, search, preview, upload, drag out, rename, move, delete). */
import { h, I, api, state, opt, saveOpt, events, toast, dialog, field, emptyState, bytes, when } from "./core.js";
import * as viewer from "./viewer.js";

const ICONS = { dir: "folder", video: "film", audio: "music", image: "image", doc: "doc", archive: "archive", code: "code", file: "file" };
const PREVIEW = new Set(["video", "audio", "image"]);
const fs = { roots: [], root: "", path: "", items: [], sel: new Set(), searching: false, query: "", writable: false };
const els = {};

export function mount(section) {
  els.roots = h("div", { class: "roots" });
  els.crumbs = h("nav", { class: "crumbs", "aria-label": "Chemin" });
  els.layout = h("button", { class: "icon-btn", title: "Changer d'affichage", "aria-label": "Changer d'affichage", onclick: () => { opt.grid = !opt.grid; saveOpt(); render(); } });
  els.actions = h("div", { class: "head-actions" });
  els.selCount = h("span");
  els.selbar = h("div", { class: "selbar", hidden: true }, els.selCount, h("div", { class: "selbar-actions" },
    h("button", { class: "btn ghost small", onclick: () => { fs.items.forEach((it) => fs.sel.add(it.name)); render(); } }, "Tout sélectionner"),
    h("button", { class: "btn ghost small", onclick: () => { fs.sel.clear(); render(); } }, "Annuler"),
    h("button", { class: "btn ghost small", onclick: moveSelection }, I("move"), "Déplacer"),
    h("button", { class: "btn danger small", onclick: () => removeItems([...fs.sel]) }, I("trash"), "Supprimer")));
  els.list = h("div", { class: "filelist" });
  els.empty = h("div", { hidden: true });
  els.drop = h("div", { class: "dropzone" },
    h("div", { class: "drop-hint" }, h("div", {}, h("span", { class: "drop-ring" }), h("p", {}, "Lâche pour envoyer ici"))),
    els.list, els.empty);
  section.append(
    h("div", { class: "view-head" }, els.roots, els.crumbs,
      h("div", { class: "head-tools" }, h("button", { class: "icon-btn", title: "Trier", "aria-label": "Trier", onclick: cycleSort }, I("sort")), els.layout),
      els.actions),
    els.selbar, els.drop);
  wireDrop();
}

export async function show(arg) {
  if (!fs.roots.length) {
    try { fs.roots = (await api("/api/fs/roots")).roots; } catch (error) {
      els.list.replaceChildren(emptyState("info", "Fichiers indisponibles", error.message));
      return;
    }
  }
  if (!fs.roots.includes(fs.root)) { fs.root = fs.roots[0] || ""; fs.path = ""; }
  if (arg && arg.root && fs.roots.includes(arg.root)) { fs.root = arg.root; fs.path = arg.path || ""; }
  renderRoots();
  await loadFiles(fs.path);
}

export const query = () => fs.query;

const relPath = (it) => {
  const base = it.dir !== undefined ? it.dir : fs.path;
  return base ? `${base}/${it.name}` : it.name;
};
const fileUrl = (it, mode = "download") => `/api/fs/${mode}?${new URLSearchParams({ root: fs.root, path: relPath(it) })}`;

async function loadFiles(path) {
  fs.searching = false;
  fs.sel.clear();
  if (!fs.root) {
    els.list.replaceChildren();
    els.empty.hidden = false;
    els.empty.replaceChildren(emptyState("folder", "Aucun emplacement", "Branche un disque ou ajoute un dossier suivi dans Disques."));
    return;
  }
  if (!els.list.children.length) els.list.replaceChildren(...Array.from({ length: 6 }, () => h("div", { class: "sk sk-row" })));
  const params = new URLSearchParams({ root: fs.root, path, hidden: opt.hidden ? "1" : "0", sort: opt.sort, desc: opt.desc ? "1" : "0" });
  try {
    const data = await api(`/api/fs/list?${params}`);
    Object.assign(fs, { path: data.path, items: data.items, writable: data.writable });
    renderCrumbs();
    renderActions();
    render();
  } catch (error) {
    toast(error.message, "err");
    if (path) loadFiles("");
  }
}

export async function search(q) {
  fs.query = q;
  if (q.length < 2) { if (fs.searching) loadFiles(fs.path); return; }
  try {
    const data = await api(`/api/fs/search?${new URLSearchParams({ root: fs.root, q, path: fs.path })}`);
    Object.assign(fs, { searching: true, items: data.items });
    fs.sel.clear();
    renderActions();
    render();
    if (data.truncated) toast("Beaucoup de résultats : les 300 premiers sont affichés.");
  } catch (error) { toast(error.message, "err"); }
}

/* ---------------------------------------------------------------- rendering */
function renderRoots() {
  els.roots.replaceChildren(...(fs.roots.length < 2 ? [] : fs.roots.map((name) => h("button", {
    class: `chip${name === fs.root ? " is-active" : ""}`,
    onclick: () => { fs.root = name; fs.path = ""; renderRoots(); loadFiles(""); },
  }, I("drive"), name))));
}

function renderCrumbs() {
  const parts = fs.path ? fs.path.split("/") : [];
  const crumb = (label, path, last) => h("button", { class: `crumb${last ? " last" : ""}`, onclick: () => loadFiles(path) }, label);
  els.crumbs.replaceChildren(crumb(fs.root, "", !parts.length),
    ...parts.flatMap((part, i) => [h("span", { class: "crumb-sep" }, "/"), crumb(part, parts.slice(0, i + 1).join("/"), i === parts.length - 1)]));
}

function renderActions() {
  els.actions.replaceChildren();
  if (!fs.writable || fs.searching) return;
  els.actions.append(
    h("button", { class: "btn ghost", onclick: makeFolder }, I("folder-plus"), "Nouveau dossier"),
    h("button", { class: "btn primary", onclick: () => {
      const input = h("input", { type: "file", multiple: true });
      input.addEventListener("change", () => uploadAll([...input.files]));
      input.click();
    } }, I("upload"), "Envoyer"));
}

function updateSelbar() {
  const n = fs.sel.size;
  els.selbar.hidden = n === 0;
  els.selCount.textContent = n === 1 ? "1 élément sélectionné" : `${n} éléments sélectionnés`;
}

function toggle(it, row) {
  if (fs.sel.has(it.name)) fs.sel.delete(it.name); else fs.sel.add(it.name);
  row.classList.toggle("selected", fs.sel.has(it.name));
  updateSelbar();
}

function render() {
  els.list.className = `filelist${opt.grid ? " grid" : ""}`;
  els.layout.replaceChildren(I(opt.grid ? "list" : "grid"));
  els.list.replaceChildren(...fs.items.map((it, i) => row(it, i)));
  els.empty.hidden = fs.items.length > 0;
  if (!fs.items.length) {
    els.empty.replaceChildren(fs.searching
      ? emptyState("search", "Rien trouvé", `Aucun nom ne contient « ${fs.query} » ici.`)
      : emptyState("folder", "Ce dossier est vide", fs.writable ? "Dépose des fichiers ici, ou utilise le bouton Envoyer." : "Ton compte est en lecture seule."));
  }
  updateSelbar();
}

function row(it, index) {
  const isDir = it.kind === "dir";
  const el = h("div", { class: `row${fs.sel.has(it.name) ? " selected" : ""}`, tabindex: "0", role: "button", "aria-label": it.name });
  if (index < 16) { el.classList.add("rise"); el.style.setProperty("--i", index); }
  const icon = h("span", { class: `ic ${it.kind}` }, I(ICONS[it.kind] || "file"));
  if (it.kind === "image") {
    const img = h("img", { src: fileUrl(it, "stream"), alt: "", loading: "lazy" });
    img.addEventListener("error", () => img.replaceWith(I("image")), { once: true });
    icon.replaceChildren(img);
  }
  const actions = h("span", { class: "row-actions" });
  el.append(icon,
    h("span", { class: "row-main" }, h("span", { class: "row-name" }, it.name),
      h("span", { class: "row-meta" }, `${it.dir ? `${it.dir} · ` : ""}${isDir ? "Dossier" : bytes(it.size)} · ${when(it.mtime)}`)),
    actions);

  const open = () => {
    if (isDir) { loadFiles(relPath(it)); return; }
    if (PREVIEW.has(it.kind)) { preview(it); return; }
    window.open(fileUrl(it), "_blank", "noopener");
  };
  el.addEventListener("click", (e) => {
    if (e.target.closest(".row-actions")) return;
    if (e.ctrlKey || e.metaKey || fs.sel.size) { toggle(it, el); return; }
    if (isDir || window.matchMedia("(max-width: 900px)").matches) open();
  });
  el.addEventListener("dblclick", () => { if (!isDir) open(); });
  el.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); open(); }
    if (e.key === " ") { e.preventDefault(); toggle(it, el); }
  });
  if (!isDir) {
    el.draggable = true;
    el.addEventListener("dragstart", (e) => {
      const url = new URL(fileUrl(it), location.origin).href;
      e.dataTransfer.effectAllowed = "copy";
      e.dataTransfer.setData("DownloadURL", `application/octet-stream:${it.name}:${url}`);
      e.dataTransfer.setData("text/uri-list", url);
      el.classList.add("dragging");
    });
    el.addEventListener("dragend", () => el.classList.remove("dragging"));
  }
  const add = (title, name, fn, cls = "") => actions.append(h("button", {
    class: `icon-btn ${cls}`, title, "aria-label": `${title} : ${it.name}`, onclick: (e) => { e.stopPropagation(); fn(); },
  }, I(name)));
  if (PREVIEW.has(it.kind)) add("Aperçu", "eye", () => preview(it));
  if (!isDir) add("Télécharger", "download", () => { location.href = fileUrl(it); });
  if (fs.writable && !fs.searching) {
    add("Renommer", "edit", () => rename(it));
    add("Supprimer", "trash", () => removeItems([it.name]), "danger");
  }
  return el;
}

function preview(it) {
  const pool = fs.items.filter((x) => PREVIEW.has(x.kind));
  const index = pool.findIndex((x) => x.name === it.name && x.dir === it.dir);
  viewer.openMedia(pool.map((x) => ({ name: x.name, kind: x.kind, url: fileUrl(x, "stream") })), Math.max(0, index));
}

/* ---------------------------------------------------------------- actions */
function cycleSort() {
  const order = ["name", "date", "size", "kind"];
  if (!opt.desc) opt.desc = true;
  else { opt.desc = false; opt.sort = order[(order.indexOf(opt.sort) + 1) % order.length]; }
  saveOpt();
  toast(`Tri par ${{ name: "nom", date: "date", size: "taille", kind: "type" }[opt.sort]}${opt.desc ? ", décroissant" : ""}`);
  loadFiles(fs.path);
}

async function makeFolder() {
  const name = field("Nom du dossier", { placeholder: "Vacances 2026", autocomplete: "off" });
  if (!await dialog({ title: "Nouveau dossier", body: name.el, ok: "Créer" }) || !name.input.value.trim()) return;
  try {
    await api("/api/fs/mkdir", { method: "POST", form: { root: fs.root, path: fs.path, name: name.input.value.trim() } });
    toast("Dossier créé.", "ok");
    loadFiles(fs.path);
  } catch (error) { toast(error.message, "err"); }
}

async function rename(it) {
  const name = field("Nouveau nom", { value: it.name, autocomplete: "off" });
  if (!await dialog({ title: "Renommer", body: name.el, ok: "Renommer" })) return;
  const next = name.input.value.trim();
  if (!next || next === it.name) return;
  try {
    await api("/api/fs/rename", { method: "POST", form: { root: fs.root, path: fs.path, name: it.name, new_name: next } });
    toast("Renommé.", "ok");
    loadFiles(fs.path);
  } catch (error) { toast(error.message, "err"); }
}

async function moveSelection() {
  const names = [...fs.sel];
  const dest = field("Dossier de destination", { value: fs.path, placeholder: "Media/Films", autocomplete: "off" }, `Chemin dans « ${fs.root} ». Vide = la racine.`);
  if (!await dialog({ title: `Déplacer ${names.length > 1 ? `${names.length} éléments` : `« ${names[0]} »`}`, body: dest.el, ok: "Déplacer" })) return;
  try {
    const result = await api("/api/fs/move", { method: "POST", form: { root: fs.root, path: fs.path, names: names.join("\n"), dest: dest.input.value.trim() } });
    toast(result.moved > 1 ? `${result.moved} éléments déplacés.` : result.moved ? "Déplacé." : "Rien n'a été déplacé (nom déjà pris ?).", result.moved ? "ok" : "err");
    loadFiles(fs.path);
  } catch (error) { toast(error.message, "err"); }
}

async function removeItems(names) {
  if (!names.length) return;
  if (opt.confirm) {
    const label = names.length === 1 ? `« ${names[0]} »` : `${names.length} éléments`;
    if (!await dialog({ title: "Supprimer", text: `${label} : suppression définitive, sans corbeille.`, ok: "Supprimer", danger: true })) return;
  }
  try {
    const result = await api("/api/fs/delete", { method: "POST", form: { root: fs.root, path: fs.path, names: names.join("\n") } });
    fs.sel.clear();
    toast(result.removed > 1 ? `${result.removed} éléments supprimés.` : "Supprimé.", "ok");
    loadFiles(fs.path);
  } catch (error) { toast(error.message, "err"); }
}

/* ---------------------------------------------------------------- uploads */
function uploadAll(list) {
  if (!list.length) return;
  if (!fs.writable) { toast("Ton compte est en lecture seule.", "err"); return; }
  document.getElementById("uploads").hidden = false;
  list.forEach(upload);
}

function upload(file) {
  const bar = h("span");
  const name = h("span", { class: "up-name" }, file.name);
  const item = h("li", {}, name, h("span", { class: "up-bar" }, bar));
  document.getElementById("uploadList").prepend(item);
  const body = new FormData();
  body.append("root", fs.root);
  body.append("path", fs.path);
  body.append("file", file);
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/fs/upload");
  xhr.setRequestHeader("X-CSRF-Token", state.csrf);
  xhr.withCredentials = true;
  xhr.upload.onprogress = (e) => { if (e.lengthComputable) bar.style.width = `${(e.loaded / e.total) * 100}%`; };
  xhr.onload = () => {
    if (xhr.status === 200) {
      item.classList.add("up-done");
      bar.style.width = "100%";
      setTimeout(() => {
        item.remove();
        if (!document.getElementById("uploadList").children.length) document.getElementById("uploads").hidden = true;
      }, 2200);
      loadFiles(fs.path);
    } else {
      item.classList.add("up-fail");
      let message = `Échec (${xhr.status})`;
      try { message = JSON.parse(xhr.responseText).detail || message; } catch { /* not JSON */ }
      name.textContent = `${file.name} : ${message}`;
    }
  };
  xhr.onerror = () => { item.classList.add("up-fail"); name.textContent = `${file.name} : connexion interrompue`; };
  xhr.send(body);
}
document.getElementById("uploadsClose").addEventListener("click", () => {
  document.getElementById("uploads").hidden = true;
  document.getElementById("uploadList").replaceChildren();
});

function wireDrop() {
  let depth = 0;
  const hasFiles = (e) => e.dataTransfer && [...e.dataTransfer.types].includes("Files");
  ["dragenter", "dragover"].forEach((type) => els.drop.addEventListener(type, (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    if (type === "dragenter") depth += 1;
    els.drop.classList.add("is-over");
  }));
  els.drop.addEventListener("dragleave", () => { depth -= 1; if (depth <= 0) { depth = 0; els.drop.classList.remove("is-over"); } });
  els.drop.addEventListener("drop", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth = 0;
    els.drop.classList.remove("is-over");
    uploadAll([...e.dataTransfer.files]);
  });
  ["dragover", "drop"].forEach((type) => document.addEventListener(type, (e) => { if (hasFiles(e)) e.preventDefault(); }));
}

/* ---------------------------------------------------------------- keys and events */
export function onKey(event) {
  if (event.key === "Backspace" && fs.path && !fs.searching) {
    event.preventDefault();
    loadFiles(fs.path.split("/").slice(0, -1).join("/"));
  }
  if (event.key === "Delete" && fs.sel.size && fs.writable) {
    event.preventDefault();
    removeItems([...fs.sel]);
  }
}

export function onEvent(message) {
  if (message.type !== "drive_added" && message.type !== "drive_removed") return;
  fs.roots = [];
  if (state.view === "files" && els.list) show();
}
events.on("files-options", () => { if (state.view === "files" && els.list) loadFiles(fs.path); });
