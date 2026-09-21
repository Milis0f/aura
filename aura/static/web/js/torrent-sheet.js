/* Aura web — the "fiche" of a search result: what the title is, then which release to take and where.

   Reuses the library's sheet so a download and a film in the collection look like the same object. */
import { h, I, bytes, when } from "./core.js";
import { targetPicker } from "./torrent-target.js";

let sheet = null;
let scrim = null;
let lastFocus = null;
let target = { mode: "server", volume: "", category: "Films" };

const FOCUSABLE = "button, a[href], input, select, textarea, [tabindex]:not([tabindex='-1'])";

/** aria-modal alone does not stop a screen reader walking the page behind; inert does. */
function background(off) {
  const app = document.querySelector(".app");
  if (!app) return;
  if (off) app.setAttribute("inert", "");
  else app.removeAttribute("inert");
  if (!("inert" in HTMLElement.prototype)) app.toggleAttribute("aria-hidden", off);
}

export function closeCard() {
  if (!sheet) return;
  sheet.remove();
  scrim.remove();
  sheet = scrim = null;
  background(false);
  document.removeEventListener("keydown", onKey);
  if (lastFocus && lastFocus.isConnected) lastFocus.focus();
}

function onKey(event) {
  if (event.key === "Escape") { event.stopPropagation(); closeCard(); return; }
  if (event.key !== "Tab" || !sheet) return;
  // Keep Tab inside the sheet: behind it the page is inert, so escaping would strand the focus ring.
  const stops = [...sheet.querySelectorAll(FOCUSABLE)].filter((el) => !el.disabled && el.offsetParent !== null);
  if (!stops.length) return;
  const edge = event.shiftKey ? stops[0] : stops[stops.length - 1];
  if (document.activeElement === edge || !sheet.contains(document.activeElement)) {
    event.preventDefault();
    (event.shiftKey ? stops[stops.length - 1] : stops[0]).focus();
  }
}

export function openCard(card, { onStarted } = {}) {
  closeCard();
  lastFocus = document.activeElement;
  target = { ...target, category: card.media === "tv" ? "Series" : "Films" };
  let chosen = card.releases[0];

  const body = h("div", { class: "sheet-body" });
  sheet = h("aside", { class: "sheet", role: "dialog", "aria-modal": "true", "aria-label": card.title }, body);
  scrim = h("div", { class: "sheet-scrim", onclick: closeCard });
  document.body.append(scrim, sheet);
  background(true);
  document.addEventListener("keydown", onKey);
  render();
  const close = sheet.querySelector(".dt-close");
  if (close) close.focus();

  function pick(release) { chosen = release; render(); }
  function onTargetChange(next) { target = next; render(); }

  function render() {
    const art = card.backdrop || card.poster;
    body.replaceChildren(
      h("header", { class: "dt-head" },
        art ? h("div", { class: "bg", style: `background-image:url("${cssUrl(art)}")` }) : null,
        h("div", { class: "veil" }),
        h("button", { class: "dt-close", "aria-label": "Fermer la fiche", onclick: closeCard }, I("close")),
        h("div", { class: "dt-top" },
          h("div", { class: "dt-poster" }, posterArt(card)),
          h("div", {},
            h("span", { class: "kicker" }, I(card.media === "tv" ? "tv" : "film"), card.media === "tv" ? "Série" : "Film"),
            h("h2", { class: "dt-title" }, card.title),
            h("div", { class: "dt-meta" }, metaBits(card))))),
      h("div", { class: "dt-body" },
        card.overview
          ? h("p", { class: "dt-overview" }, card.overview)
          : h("p", { class: "dt-overview dim" }, card.enriched
            ? "Aucun résumé dans le catalogue pour ce titre."
            : "Titre absent du catalogue : ce qui est affiché vient du nom du fichier."),
        releasesSection(),
        h("section", { class: "dt-section" },
          h("h3", {}, "Destination"),
          targetPicker({ value: target, onChange: onTargetChange, release: chosen, onStarted }))));
  }

  function releasesSection() {
    return h("section", { class: "dt-section" },
      h("h3", {}, card.releases.length > 1 ? `${card.releases.length} versions` : "Version"),
      card.releases.map((release) => {
        const on = release.id === chosen.id;
        return h("label", { class: `version${on ? " is-on" : ""}` },
          h("div", { class: "grow" },
            h("div", { class: "ep-sub" },
              h("span", { class: "num" }, release.size ? bytes(release.size) : "taille inconnue"),
              h("span", { class: release.seeders ? "res-seed" : "dim" },
                release.seeders == null ? "sources inconnues" : `${release.seeders} sources`),
              release.indexer ? h("span", {}, release.indexer) : null,
              published(release)),
            h("div", { class: "ep-file", title: release.name }, release.name),
            release.details_url
              ? h("a", { class: "res-link", href: release.details_url, target: "_blank", rel: "noreferrer noopener", onclick: (e) => e.stopPropagation() }, "Voir sur la source")
              : null),
          // A radio, and it has to look like one: a switch would read as "several at once".
          h("input", {
            type: "radio", name: "aura-release", class: "pick", checked: on,
            "aria-label": `Choisir ${release.name}`, onchange: () => pick(release),
          }));
      }));
  }
}

/** Indexers write dates in every format there is: show a real one, or the raw text, never 1970. */
function published(release) {
  if (!release.published) return null;
  const ms = Date.parse(release.published);
  return h("span", {}, Number.isNaN(ms) ? release.published.slice(0, 10) : when(ms / 1000));
}

function metaBits(card) {
  const bits = [];
  if (card.year) bits.push(h("span", {}, card.year));
  if (card.rating) bits.push(h("span", { class: "rating" }, I("star-fill"), String(card.rating).replace(".", ",").slice(0, 3)));
  if (card.size) bits.push(h("span", {}, bytes(card.size)));
  if (card.seeders) bits.push(h("span", {}, `${card.seeders} sources`));
  (card.genres || []).forEach((genre) => bits.push(h("span", { class: "tag" }, genre)));
  if (!card.enriched) bits.push(h("span", { class: "tag warn" }, "hors catalogue"));
  return bits;
}

/** Poster when the catalogue has one, initials on a generated gradient when it does not. */
export function posterArt(card) {
  if (card.poster) return h("img", { src: card.poster, alt: "", loading: "lazy", decoding: "async" });
  return h("div", { class: "pc-fallback", style: `--h:${card.hue}` }, h("span", {}, card.initials));
}

/** The poster URL lands inside url("…"): quotes, backslashes and parens must not survive. */
export const cssUrl = (url) => String(url).replace(/["\\()]/g, "");
