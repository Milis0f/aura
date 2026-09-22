/* Aura web — the search results: a grid of titles, not a list of file names.

   One card per title, its releases folded underneath (the sheet lets the owner pick one). Every state
   has something on screen: skeletons while the box searches, a reason when it fails, a way forward when
   it finds nothing. */
import { h, I, api, toast, busy, bytes, stagger, emptyState } from "./core.js";
import { openCard, posterArt } from "./torrent-sheet.js";
import { flyToDownloads } from "./fly-to-downloads.js";

const SORTS = [
  { key: "seeders", label: "Sources", dir: "desc" },
  { key: "size", label: "Taille", dir: "desc" },
  { key: "year", label: "Année", dir: "desc" },
  { key: "title", label: "Titre", dir: "asc" },
];
const PAGE = 40;
const ALL = 200;

export function resultGrid({ onSearch, onStarted }) {
  const box = h("div", { class: "results" });
  const view = { status: "idle", cards: [], warnings: [], error: "", limit: PAGE, truncated: false };
  let sort = { key: "seeders", dir: "desc" };

  return { el: box, set, render };

  function set(patch) { Object.assign(view, patch); render(); }

  /** Empty values sink to the bottom whichever way the column points. */
  function sorted() {
    const factor = sort.dir === "asc" ? 1 : -1;
    return [...view.cards].sort((left, right) => {
      const a = value(left), b = value(right);
      if (a == null && b == null) return 0;
      if (a == null) return 1;
      if (b == null) return -1;
      return (typeof a === "string" ? a.localeCompare(b) : a - b) * factor;
    });
    function value(card) {
      if (sort.key === "title") return (card.title || "").toLowerCase();
      if (sort.key === "year") return card.year ? Number(card.year) : null;
      return card[sort.key];
    }
  }

  function toolbar() {
    const select = h("select", {
      "aria-label": "Trier les résultats",
      onchange: (event) => {
        const found = SORTS.find((item) => item.key === event.target.value);
        sort = { key: found.key, dir: found.dir };
        render();
      },
    }, SORTS.map((item) => h("option", { value: item.key, selected: sort.key === item.key }, item.label)));
    const flip = h("button", {
      class: "btn ghost icon-only", title: sort.dir === "asc" ? "Croissant" : "Décroissant",
      "aria-label": `Tri ${sort.dir === "asc" ? "croissant" : "décroissant"}, inverser`,
      onclick: () => { sort = { ...sort, dir: sort.dir === "asc" ? "desc" : "asc" }; render(); },
    }, I(sort.dir === "asc" ? "chevron-up" : "chevron-down"));
    const count = `${view.cards.length} ${view.cards.length > 1 ? "titres" : "titre"}`;
    return h("div", { class: "res-bar" },
      // Announced on its own: a screen reader otherwise gets no sign that the results changed.
      h("span", { class: "res-count", role: "status", "aria-live": "polite" }, count),
      h("span", { class: "spacer" }),
      view.truncated ? h("button", { class: "btn ghost", onclick: (event) => busy(event.currentTarget, () => onSearch(ALL)) }, I("list"), "Voir tout") : null,
      select, flip);
  }

  function card(entry, index) {
    const art = h("div", { class: "pc-art" },
      posterArt(entry),
      h("div", { class: "pc-badges" },
        entry.rating ? h("span", { class: "tag hi" }, I("star-fill"), String(entry.rating).replace(".", ",").slice(0, 3)) : null,
        entry.media === "tv" ? h("span", { class: "tag" }, "Série") : null,
        entry.releases.length > 1 ? h("span", { class: "tag" }, `${entry.releases.length} versions`) : null),
      // Every card repeats the same two words, so the accessible name has to carry the title - which
      // also lets the label drop on narrow cards without the buttons becoming anonymous.
      h("div", { class: "pc-actions" },
        h("button", { class: "btn small", title: "Fiche", "aria-label": `Fiche de ${entry.title}`, onclick: () => openCard(entry, { onStarted }) },
          I("info"), h("span", { class: "lbl" }, "Fiche")),
        h("button", { class: "btn small primary", title: "Télécharger", "aria-label": `Télécharger ${entry.title}`, onclick: (event) => busy(event.currentTarget, () => grab(entry, event.currentTarget.closest(".poster-card"))) },
          I("download"), h("span", { class: "lbl" }, "Télécharger"))));
    const bits = [entry.year, entry.size ? bytes(entry.size) : "", entry.seeders == null ? "" : `${entry.seeders} sources`];
    return stagger(h("article", { class: "poster-card" }, art,
      h("div", { class: "pc-meta" },
        h("div", { class: "pc-title", title: entry.title }, entry.title),
        h("div", { class: "pc-sub" }, bits.filter(Boolean).join(" · ")))), index);
  }

  async function grab(entry, card) {
    const release = entry.releases[0];
    await api("/api/torrents/add", {
      method: "POST",
      form: { magnet: release.magnet || release.torrent_url, category: entry.media === "tv" ? "Series" : "Films", volume: "" },
    });
    flyToDownloads(card);
    toast(`« ${entry.title} » part dans qBittorrent.`, "ok", "download");
    if (onStarted) onStarted();
  }

  function render() {
    if (view.status === "idle") {
      box.replaceChildren(emptyState("search", "Cherche un titre",
        "Deux lettres suffisent. Aura interroge le catalogue public de l'Internet Archive, et ton indexeur auto-hébergé s'il est configuré (AURA_INDEXER_URL)."));
      return;
    }
    if (view.status === "loading") {
      box.replaceChildren(h("div", { class: "poster-grid" },
        ...Array.from({ length: 12 }, () => h("div", {}, h("div", { class: "sk sk-poster" }), h("div", { class: "sk sk-line" }), h("div", { class: "sk sk-line", style: "width:55%" })))));
      return;
    }
    if (view.status === "error") {
      box.replaceChildren(emptyState("info", "Recherche impossible", view.error, {
        actions: [h("button", { class: "btn", onclick: (event) => busy(event.currentTarget, () => onSearch(view.limit)) }, I("refresh"), "Réessayer")],
      }));
      return;
    }
    if (!view.cards.length) {
      box.replaceChildren(
        emptyState("search", "Aucun résultat",
          "Essaie un autre mot. Le catalogue public couvre les films du domaine public, les logiciels libres et les jeux de données ; pour le reste, configure ton propre indexeur."),
        ...warnings());
      return;
    }
    box.replaceChildren(toolbar(), h("div", { class: "poster-grid" }, sorted().map(card)), ...warnings());
  }

  function warnings() {
    return view.warnings.map((warning) => h("p", { class: "res-warn" }, I("info"), `${warning.source} : ${warning.message}`));
  }
}
