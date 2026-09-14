/* Aura web — the library: films and series found on the drives, films added by link. Play on the TV or here. */
import { $, h, I, api, opt, saveOpt, state, events, toast, dialog, field, busy, emptyState, stagger, duration, clock, episodeCode, bytes } from "./core.js";
import * as viewer from "./viewer.js";

const KIND_ICON = { film: "film", series: "stack", link: "link" };
const KIND_LABEL = { film: "Film", series: "Série", link: "Lien" };
const PAGE = 48;
const lib = { kind: opt.libKind, sort: opt.libSort, all: Boolean(opt.libOffline), drive: "", q: "", offset: 0, total: 0, home: null };
let heroBox, toolbar, rowsBox, gridHead, grid, moreBox;
let gridToken = 0;
let detailItem = null;
let refreshTimer = null;

const pct = (p) => Math.round(Math.min(1, Math.max(0, p || 0)) * 100);
const cssUrl = (url) => String(url).replace(/["\\\n]/g, (c) => `\\${c}`);
const hostOf = (url) => { try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return "lien"; } };
const plural = (n, word) => `${n} ${word}${n > 1 ? "s" : ""}`;

export function mount(section) {
  heroBox = h("div");
  toolbar = h("div", { class: "lib-toolbar" });
  rowsBox = h("div");
  gridHead = h("h2", { class: "section-title" });
  grid = h("div", { class: "poster-grid" });
  moreBox = h("div", { class: "lib-more" });
  section.append(heroBox, toolbar, rowsBox, gridHead, grid, moreBox);
  renderToolbar([]);
}

export async function show(arg) {
  if (arg && arg.drive !== undefined) {
    lib.drive = arg.drive || "";
    lib.all = true;
  }
  await refresh();
}

export const query = () => lib.q;

export function search(q) {
  lib.q = q;
  if (lib.home) { renderHero(lib.home); renderRows(lib.home); }
  loadGrid(true);
}

async function refresh() {
  if (!lib.home) heroBox.replaceChildren(h("div", { class: "sk sk-hero" }));
  try {
    const home = await api(`/api/library/home${lib.all ? "?all=1" : ""}`);
    lib.home = home;
    renderHero(home);
    renderRows(home);
    renderToolbar(home.drives);
  } catch (error) {
    heroBox.replaceChildren(emptyState("info", "Bibliothèque indisponible", error.message));
  }
  await loadGrid(true);
}

/* ---------------------------------------------------------------- pieces */
const placeholder = (it) => h("div", { class: "ph" },
  h("span", { class: "ph-kind" }, I(KIND_ICON[it.kind] || "film")),
  h("span", { class: "ph-title" }, it.title),
  it.year ? h("span", { class: "ph-year num" }, it.year) : null);

function artImg(it, src = it.poster) {
  if (!src) return placeholder(it);
  const img = h("img", { src, alt: "", loading: "lazy", decoding: "async" });
  img.addEventListener("error", () => img.replaceWith(placeholder(it)), { once: true });
  return img;
}

function metaBits(it) {
  const bits = [];
  if (it.year) bits.push(h("span", { class: "num" }, it.year));
  if (it.kind === "series" && it.seasons) bits.push(h("span", {}, plural(it.seasons, "saison")));
  const time = it.runtime ? duration(it.runtime * 60) : duration(it.duration);
  if (time && it.kind !== "series") bits.push(h("span", {}, time));
  if (it.rating) bits.push(h("span", { class: "pill" }, I("star-fill"), it.rating.toFixed(1)));
  if (it.quality) bits.push(h("span", { class: "tag hi" }, it.quality));
  it.langs.forEach((code) => bits.push(h("span", { class: "tag" }, code)));
  if (it.genres.length) bits.push(h("span", {}, it.genres.slice(0, 2).join(" · ")));
  return bits;
}

function subline(it) {
  if (!it.online) return "Disque débranché";
  if (it.kind === "series") return [it.year, it.seasons ? plural(it.seasons, "saison") : ""].filter(Boolean).join(" · ") || "Série";
  if (it.kind === "link") return [it.year, hostOf(it.url)].filter(Boolean).join(" · ");
  return [it.year, duration(it.runtime * 60 || it.duration)].filter(Boolean).join(" · ") || it.drives[0] || "Film";
}

/* ---------------------------------------------------------------- hero, rows, toolbar */
function onboarding(home) {
  const plugged = home.drives.some((d) => d.available);
  return emptyState("usb", plugged ? "Aucun film trouvé pour l'instant" : "Branche un disque dur",
    plugged
      ? "Les disques branchés sont analysés. Les vidéos de plus de quelques minutes apparaissent ici, rangées en films et en séries."
      : "Aura le détecte tout seul, cherche les films et les séries, et les affiche ici avec l'affiche et le résumé.",
    {
      steps: plugged ? [] : ["Branche le disque en USB sur le boîtier.", "Une notification apparaît en quelques secondes.", "Les titres arrivent pendant l'analyse, sans rien toucher."],
      actions: [
        h("button", { class: "btn primary", onclick: () => addLink() }, I("link"), "Ajouter un film par lien"),
        h("button", { class: "btn", onclick: () => events.emit("navigate", { view: "drives" }) }, I("drive"), "Voir les disques"),
        h("button", { class: "btn ghost", onclick: (e) => busy(e.currentTarget, rescan) }, I("scan"), "Analyser maintenant"),
      ],
    });
}

function renderHero(home) {
  if (!home.total && !lib.all) { heroBox.replaceChildren(onboarding(home)); return; }
  const it = home.hero;
  if (!it || lib.q || lib.drive) { heroBox.replaceChildren(); return; }
  const resume = Boolean(it.resume && !it.resume.finished && it.resume.position > 30);
  const art = it.backdrop || it.poster;
  heroBox.replaceChildren(h("section", { class: "lib-hero rise" },
    art ? h("div", { class: "bg", style: `background-image:url("${cssUrl(art)}")` }) : null,
    h("div", { class: "veil" }),
    h("div", { class: "txt" },
      h("span", { class: "kicker" }, I(resume ? "play-circle" : "film"), resume ? "Reprendre" : "Ajouté récemment"),
      h("h2", {}, it.title),
      h("div", { class: "hero-meta" }, metaBits(it)),
      it.overview ? h("p", { class: "hero-overview" }, it.overview) : null,
      resume ? h("div", { class: "hero-progress" },
        h("div", { class: "meter" }, h("i", { style: `width:${pct(it.resume.progress)}%` })),
        h("small", { class: "muted num" }, `${clock(it.resume.position)} / ${clock(it.resume.duration)}`)) : null,
      h("div", { class: "hero-actions" },
        it.online ? h("button", { class: "btn primary", onclick: (e) => busy(e.currentTarget, () => playOnTv(it.id)) }, I("tv"), resume ? "Reprendre sur la TV" : "Lire sur la TV") : null,
        it.online ? h("button", { class: "btn", onclick: () => playHere(it.id) }, I("play"), "Lire ici") : null,
        h("button", { class: "btn ghost", onclick: () => openDetail(it.id) }, I("info"), "Détails"))),
    h("div", { class: "art" }, h("div", { class: "frame" }, artImg(it)))));
}

function wideCard(it) {
  const r = it.resume || {};
  const left = r.duration ? Math.max(0, r.duration - r.position) : 0;
  const parts = [r.episode ? episodeCode(r.season, r.episode) : "", r.position > 0 && left ? `${duration(left) || "moins d'une minute"} restantes` : "Épisode suivant"];
  return h("button", { class: "wide-card", onclick: () => openDetail(it.id) },
    h("div", { class: "wc-art" }, artImg(it, it.backdrop || it.poster), r.progress ? h("div", { class: "meter" }, h("i", { style: `width:${pct(r.progress)}%` })) : null),
    h("div", { class: "wc-body" }, h("div", { class: "wc-title" }, it.title), h("div", { class: "wc-sub" }, parts.filter(Boolean).join(" · "))));
}

function renderRows(home) {
  const resume = home.rows.find((row) => row.key === "continue");
  if (!resume || lib.q || lib.drive) { rowsBox.replaceChildren(); return; }
  rowsBox.replaceChildren(
    h("h2", { class: "section-title" }, "Reprendre", h("span", { class: "count" }, String(resume.items.length))),
    h("div", { class: "hscroll" }, resume.items.map((it, i) => stagger(wideCard(it), i))));
}

function renderToolbar(drives) {
  const kinds = [["all", "Tout"], ["film", "Films"], ["series", "Séries"], ["link", "Liens"]];
  const seg = h("div", { class: "seg" }, kinds.map(([key, label]) => h("button", {
    class: lib.kind === key ? "is-active" : "",
    onclick: () => { lib.kind = key; opt.libKind = key; saveOpt(); renderToolbar(drives); loadGrid(true); },
  }, label)));
  const pickDrive = (id) => { lib.drive = id; renderToolbar(drives); if (lib.home) { renderHero(lib.home); renderRows(lib.home); } loadGrid(true); };
  const chips = drives.length > 1 || lib.drive ? h("div", { class: "chips-row" },
    h("button", { class: `chip${lib.drive ? "" : " is-active"}`, onclick: () => pickDrive("") }, "Tous les disques"),
    drives.map((d) => h("button", { class: `chip${lib.drive === d.id ? " is-active" : ""}`, onclick: () => pickDrive(d.id) },
      I(d.kind === "folder" ? "folder" : "drive"), d.label, d.available ? null : h("span", { class: "dim" }, "débranché")))) : null;
  const sort = h("select", { "aria-label": "Trier", onchange: (e) => { lib.sort = e.target.value; opt.libSort = lib.sort; saveOpt(); loadGrid(true); } },
    [["added", "Récents"], ["title", "A à Z"], ["year", "Année"], ["rating", "Note"]].map(([value, label]) => h("option", { value, selected: lib.sort === value }, label)));
  const offline = h("label", { class: "toggle" },
    h("input", { type: "checkbox", class: "sw", checked: lib.all, onchange: (e) => { lib.all = e.target.checked; opt.libOffline = lib.all; saveOpt(); refresh(); } }),
    "Disques débranchés");
  toolbar.replaceChildren(...[seg, chips, h("span", { class: "spacer" }), offline, sort,
    h("button", { class: "btn", onclick: () => addLink() }, I("link"), "Ajouter un lien"),
    h("button", { class: "btn ghost icon-only", title: "Analyser les disques", "aria-label": "Analyser les disques", onclick: (e) => busy(e.currentTarget, rescan) }, I("scan"))].filter(Boolean));
}

/* ---------------------------------------------------------------- grid */
function posterCard(it) {
  const inProgress = it.resume && !it.resume.finished && it.resume.progress > 0.01;
  const art = h("div", { class: "pc-art" },
    artImg(it),
    h("div", { class: "pc-badges" },
      it.quality ? h("span", { class: `tag${it.quality === "4K" ? " hi" : ""}` }, it.quality) : null,
      it.langs.slice(0, 2).map((code) => h("span", { class: "tag" }, code)),
      it.online ? null : h("span", { class: "tag warn" }, "hors ligne")),
    inProgress ? h("div", { class: "meter" }, h("i", { style: `width:${pct(it.resume.progress)}%` })) : null,
    it.online ? h("span", {
      class: "pc-play", title: opt.playTarget === "here" ? "Lire ici" : "Lire sur la TV",
      onclick: (e) => { e.stopPropagation(); if (opt.playTarget === "here") playHere(it.id); else playOnTv(it.id); },
    }, I(opt.playTarget === "here" ? "play" : "tv")) : null);
  return h("button", { class: `poster-card${it.online ? "" : " offline"}`, dataset: { id: it.id }, "aria-label": it.title, onclick: () => openDetail(it.id) },
    art, h("div", { class: "pc-meta" }, h("div", { class: "pc-title" }, it.title), h("div", { class: "pc-sub" }, subline(it))));
}

async function loadGrid(reset) {
  const token = ++gridToken;
  if (reset) {
    lib.offset = 0;
    grid.replaceChildren(...Array.from({ length: 12 }, () => h("div", {}, h("div", { class: "sk sk-poster" }), h("div", { class: "sk sk-line" }))));
    moreBox.replaceChildren();
  }
  const params = new URLSearchParams({ sort: lib.sort, limit: String(PAGE), offset: String(lib.offset) });
  if (lib.kind !== "all") params.set("kind", lib.kind);
  if (lib.q) params.set("q", lib.q);
  if (lib.drive) params.set("drive", lib.drive);
  if (lib.all) params.set("all", "1");
  let data;
  try { data = await api(`/api/library/items?${params}`); } catch (error) {
    if (token === gridToken) grid.replaceChildren(emptyState("info", "Chargement impossible", error.message));
    return;
  }
  if (token !== gridToken) return;
  lib.total = data.total;
  if (reset) grid.replaceChildren();
  const title = lib.q ? `Résultats pour « ${lib.q} »` : { all: "Tous les titres", film: "Films", series: "Séries", link: "Mes liens" }[lib.kind];
  gridHead.replaceChildren(title, h("span", { class: "count" }, String(data.total)));
  const libraryEmpty = lib.home && !lib.home.total && !lib.all;
  gridHead.hidden = Boolean(libraryEmpty && !lib.q);
  if (!data.total) {
    if (!libraryEmpty || lib.q) {
      grid.replaceChildren(lib.q
        ? emptyState("search", "Rien trouvé", "Essaie un autre mot, ou affiche aussi les disques débranchés.")
        : emptyState("film", "Rien dans cette catégorie", lib.kind === "link" ? "Ajoute un film par lien : il sera lisible sur la TV comme ici." : "Change de filtre, ou copie des vidéos sur un disque."));
    }
    moreBox.replaceChildren();
    return;
  }
  data.items.forEach((it, i) => grid.append(stagger(posterCard(it), reset ? i : 0)));
  lib.offset += data.items.length;
  moreBox.replaceChildren(lib.offset < data.total
    ? h("button", { class: "btn", onclick: (e) => busy(e.currentTarget, () => loadGrid(false)) }, `Afficher plus (${data.total - lib.offset})`)
    : "");
}

/* ---------------------------------------------------------------- playback */
function allFiles(item) {
  if (item.versions) return item.versions;
  return (item.seasons_list || []).flatMap((season) => season.episodes);
}

export async function playOnTv(itemId, fileId = "", position = null) {
  const body = { item_id: itemId || "", file_id: fileId || "" };
  if (position !== null && position !== undefined) body.position = position;
  try {
    await api("/api/library/play", { method: "POST", json: body });
    toast("Lecture lancée sur la TV.", "ok", "tv");
  } catch (error) { toast(error.message, "err"); }
}

export async function playHere(itemId, fileId = "", position = null) {
  let item;
  try { item = await api(`/api/library/items/${itemId}`); } catch (error) { toast(error.message, "err"); return; }
  const file = fileId ? allFiles(item).find((f) => f.id === fileId) : null;
  const target = fileId ? { file_id: fileId, position: file && !file.finished ? file.position : 0 } : item.play;
  if (!target) {
    toast(item.drives.length ? `Branche le disque « ${item.drives[0]} » pour lire ce titre.` : "Ce titre n'est pas disponible.", "err");
    return;
  }
  const chosen = file || allFiles(item).find((f) => f.id === target.file_id);
  const subtitle = chosen && chosen.episode
    ? `${episodeCode(chosen.season, chosen.episode)}${chosen.title ? ` · ${chosen.title}` : ""}`
    : [item.year, item.quality].filter(Boolean).join(" · ");
  const src = target.file_id.startsWith("link:") ? item.url : `/api/library/stream/${target.file_id}`;
  viewer.playVideo({
    src, title: item.title, subtitle, fileId: target.file_id, itemId,
    start: position ?? target.position ?? 0,
    onTv: (at) => playOnTv(itemId, target.file_id, at),
  });
}
events.on("play-here", ({ itemId, fileId, position }) => playHere(itemId, fileId, position));

/* ---------------------------------------------------------------- title sheet */
export function closeDetail() {
  $("#detail").hidden = true;
  $("#detailScrim").hidden = true;
  detailItem = null;
}
$("#detailScrim").addEventListener("click", closeDetail);

export async function openDetail(id) {
  $("#detailBody").replaceChildren(
    h("div", { class: "dt-head" }, h("div", { class: "dt-top" }, h("div", { class: "sk sk-poster" }),
      h("div", {}, h("div", { class: "sk sk-line", style: "width:70%;height:26px" }), h("div", { class: "sk sk-line", style: "width:40%" })))),
    h("div", { class: "dt-body" }, h("div", { class: "sk sk-block" })));
  $("#detail").hidden = false;
  $("#detailScrim").hidden = false;
  try { detailItem = await api(`/api/library/items/${id}`); } catch (error) { toast(error.message, "err"); closeDetail(); return; }
  renderDetail(detailItem);
  const first = $("#detailBody .dt-actions .btn");
  if (first) first.focus();
}

function renderDetail(it) {
  const play = it.play;
  const label = !play ? "" : play.resume ? `Reprendre à ${clock(play.position)}` : play.episode ? `Lire ${episodeCode(play.season, play.episode)}` : "Lire";
  const actions = h("div", { class: "dt-actions" });
  if (play) {
    actions.append(
      h("button", { class: "btn primary", onclick: (e) => busy(e.currentTarget, () => playOnTv(it.id, play.file_id, play.position)) }, I("tv"), `${label} sur la TV`),
      h("button", { class: "btn", onclick: () => { closeDetail(); playHere(it.id, play.file_id, play.position); } }, I("play"), "Ici"));
    if (play.resume) actions.append(h("button", { class: "btn ghost", onclick: (e) => busy(e.currentTarget, () => playOnTv(it.id, play.file_id, 0)) }, I("restart"), "Depuis le début"));
  }
  if (it.kind === "link") actions.append(h("button", { class: "btn ghost danger", onclick: () => removeLink(it) }, I("trash"), "Retirer"));
  const art = it.backdrop || it.poster;
  $("#detailBody").replaceChildren(
    h("header", { class: "dt-head" },
      art ? h("div", { class: "bg", style: `background-image:url("${cssUrl(art)}")` }) : null,
      h("div", { class: "veil" }),
      h("button", { class: "dt-close", "aria-label": "Fermer", onclick: closeDetail }, I("close")),
      h("div", { class: "dt-top" },
        h("div", { class: "dt-poster" }, artImg(it)),
        h("div", {},
          h("span", { class: "kicker" }, I(KIND_ICON[it.kind]), KIND_LABEL[it.kind]),
          h("h2", { class: "dt-title" }, it.title),
          h("div", { class: "dt-meta" }, metaBits(it))))),
    h("div", { class: "dt-body" },
      it.online ? null : h("div", { class: "callout" }, I("drive"),
        h("span", {}, it.drives.length ? `Branche le disque « ${it.drives.join(" » ou « ")} » pour lire ce titre.` : "Ce titre n'est plus disponible.")),
      actions,
      it.overview
        ? h("p", { class: "dt-overview" }, it.overview)
        : h("p", { class: "dt-overview dim" }, it.kind === "link" ? it.url : "Pas encore de résumé : Aura cherche l'affiche et le résumé dès que le boîtier est en ligne."),
      it.kind === "series" ? seasonsSection(it) : null,
      it.kind === "film" ? versionsSection(it) : null));
}

function versionsSection(it) {
  if (!it.versions || !it.versions.length) return null;
  return h("section", { class: "dt-section" },
    h("h3", {}, it.versions.length > 1 ? `${it.versions.length} versions` : "Fichier"),
    it.versions.map((v) => {
      const resumeAt = v.finished ? 0 : v.position;
      return h("div", { class: `version${v.online ? "" : " offline"}` },
        h("div", { class: "grow" },
          h("div", { class: "ep-sub" },
            v.quality ? h("span", { class: "tag hi" }, v.quality) : null,
            v.langs.map((code) => h("span", { class: "tag" }, code)),
            h("span", { class: "num" }, bytes(v.size)),
            v.duration ? h("span", {}, duration(v.duration)) : null,
            h("span", {}, v.drive)),
          h("div", { class: "ep-file", title: v.folder ? `${v.folder}/${v.name}` : v.name }, v.name),
          v.position > 30 && !v.finished && v.duration ? h("div", { class: "meter" }, h("i", { style: `width:${pct(v.position / v.duration)}%` })) : null),
        v.online ? h("div", { class: "inline-actions" },
          h("button", { class: "icon-btn", title: "Sur la TV", "aria-label": "Lire sur la TV", onclick: () => playOnTv(it.id, v.id, resumeAt) }, I("tv")),
          h("button", { class: "icon-btn", title: "Lire ici", "aria-label": "Lire ici", onclick: () => { closeDetail(); playHere(it.id, v.id, resumeAt); } }, I("play")),
          h("button", { class: "icon-btn", title: "Ouvrir le dossier", "aria-label": "Ouvrir le dossier", onclick: () => { closeDetail(); events.emit("navigate", { view: "files", arg: { root: v.drive, path: v.folder } }); } }, I("folder"))) : h("span"));
    }));
}

function episodeRow(it, ep) {
  const progress = ep.duration && ep.position > 30 && !ep.finished ? ep.position / ep.duration : 0;
  const code = episodeCode(ep.season, ep.episode);
  return h("div", { class: `episode${ep.finished ? " seen" : ""}${ep.online ? "" : " offline"}` },
    h("div", { class: "ep-num" }, ep.finished ? I("check") : String(ep.episode)),
    h("div", { class: "grow" },
      h("div", { class: "ep-title" }, ep.title || `Épisode ${ep.episode}`),
      h("div", { class: "ep-sub" },
        h("span", { class: "num" }, code),
        ep.duration ? h("span", {}, duration(ep.duration)) : null,
        ep.quality ? h("span", { class: "tag" }, ep.quality) : null,
        ep.versions > 1 ? h("span", {}, `${ep.versions} versions`) : null,
        ep.online ? null : h("span", {}, "débranché")),
      progress ? h("div", { class: "meter" }, h("i", { style: `width:${pct(progress)}%` })) : null),
    ep.online ? h("div", { class: "inline-actions" },
      h("button", { class: "icon-btn", title: "Sur la TV", "aria-label": `Lire ${code} sur la TV`, onclick: () => playOnTv(it.id, ep.id, progress ? ep.position : 0) }, I("tv")),
      h("button", { class: "icon-btn", title: "Lire ici", "aria-label": `Lire ${code} ici`, onclick: () => { closeDetail(); playHere(it.id, ep.id, progress ? ep.position : 0); } }, I("play"))) : h("span"));
}

function seasonsSection(it) {
  const seasons = it.seasons_list || [];
  if (!seasons.length) return null;
  const list = h("div");
  const tabs = h("div", { class: "seg" });
  const select = (season) => {
    [...tabs.children].forEach((button) => button.classList.toggle("is-active", Number(button.dataset.season) === season.season));
    list.replaceChildren(...season.episodes.map((ep) => episodeRow(it, ep)));
  };
  seasons.forEach((season) => tabs.append(h("button", { dataset: { season: String(season.season) }, onclick: () => select(season) }, season.season ? `Saison ${season.season}` : "Bonus")));
  select((it.play && seasons.find((s) => s.season === it.play.season)) || seasons[0]);
  return h("section", { class: "dt-section" },
    h("h3", {}, "Épisodes", h("span", { class: "dim num" }, String(seasons.reduce((n, s) => n + s.episodes.length, 0)))),
    seasons.length > 1 ? h("div", { class: "chips-row", style: "margin-bottom:12px" }, tabs) : null,
    list);
}

/* ---------------------------------------------------------------- links and scans */
async function addLink() {
  const url = field("Adresse de la vidéo", { type: "url", placeholder: "https://…/film.mp4", autocomplete: "off", required: true }, "MP4, WebM ou HLS (.m3u8). Le lien reste sur le boîtier.");
  const title = field("Titre (facultatif)", { placeholder: "Le Voyage dans la Lune (1902)", autocomplete: "off" }, "Laisse vide : Aura le déduit du nom du fichier.");
  if (!await dialog({ title: "Ajouter un film par lien", body: h("div", {}, url.el, title.el), ok: "Ajouter" })) return;
  try {
    const item = await api("/api/library/links", { method: "POST", json: { url: url.input.value.trim(), title: title.input.value.trim() } });
    toast(`« ${item.title} » ajouté à la bibliothèque.`, "ok", "link");
    refresh();
  } catch (error) { toast(error.message, "err"); }
}

async function removeLink(it) {
  if (!await dialog({ title: "Retirer ce lien", text: `« ${it.title} » disparaît de la bibliothèque. La vidéo en ligne n'est pas touchée.`, ok: "Retirer", danger: true })) return;
  try {
    await api(`/api/library/links/${it.id}`, { method: "DELETE" });
    closeDetail();
    toast("Lien retiré.", "ok");
    refresh();
  } catch (error) { toast(error.message, "err"); }
}

async function rescan() {
  const result = await api("/api/library/scan", { method: "POST" });
  toast(result.scheduled ? "Analyse des disques lancée." : "Une analyse est déjà en cours.", "ok", "scan");
}

/* ---------------------------------------------------------------- live updates */
export function onEvent(message) {
  const relevant = ["library_changed", "library_meta", "drive_added", "drive_removed"].includes(message.type)
    || (message.type === "library_scan" && message.state === "done");
  if (!relevant || state.view !== "library" || !grid) return;
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => {
    refresh();
    if (detailItem && !$("#detail").hidden) api(`/api/library/items/${detailItem.id}`).then((it) => { detailItem = it; renderDetail(it); }).catch(() => {});
  }, 800);
}
events.on("library-progress", () => { if (state.view === "library" && grid) refresh(); });
events.on("viewer-closed", () => { if (state.view === "library" && grid) setTimeout(refresh, 400); });
