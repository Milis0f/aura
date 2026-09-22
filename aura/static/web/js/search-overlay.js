/* Aura web — the field is the interface.

   There are no bars any more, so this one element does both jobs: it searches the library, and it is how
   you reach every other screen. Type "disq" and Disques is offered above the films.

   The field is never moved by script. It lives outside the app shell and CSS decides where it sits -
   large and centred on the home, called up from anywhere else. The previous version flew it from the top
   bar with a FLIP, which is what kept dropping the caret mid-animation. */
import { $, h, I, api, debounce } from "./core.js";
import { openDetail } from "./library.js";

const LIMIT = 12;

let wrap, input, clear, scrim, panel, jumps, list, hint;
let navigate = null;
let targets = () => [];
let open = false;
let run = 0;
let abort = null;

export function init({ reachable, go } = {}) {
  wrap = $("#searchwrap");
  input = $("#search");
  clear = $("#searchClear");
  if (!wrap || !input) return;
  if (reachable) targets = reachable;
  navigate = go;

  scrim = h("div", { class: "search-scrim", hidden: true, onclick: close });
  jumps = h("div", { class: "sr-jumps" });
  hint = h("p", { class: "sr-hint" });
  list = h("div", { class: "sr-row" });
  panel = h("div", { class: "search-panel", hidden: true }, jumps, hint, list);
  document.body.append(scrim, panel);

  input.addEventListener("focus", show);
  input.addEventListener("input", debounce(() => query(input.value.trim()), 240));
  if (clear) clear.addEventListener("click", () => { input.value = ""; clear.hidden = true; query(""); input.focus(); });
  // Handled on the document rather than on the field: Enter has to work whatever holds the focus,
  // and the overlay is modal anyway.
  document.addEventListener("keydown", (event) => {
    if (!open) return;
    if (event.key === "Escape") { event.stopPropagation(); close(); return; }
    if (event.key === "Enter" || event.key === "NumpadEnter") {
      const first = panel.querySelector(".sr-jump, .sr-card");
      if (first) { event.preventDefault(); event.stopPropagation(); first.click(); }
    }
  });
}

export const isOpen = () => open;

/** Called by the one floating button and by Ctrl+K. */
export function open_() { show(); input.focus(); }
export { open_ as open };

function show() {
  if (open) return;
  open = true;
  document.body.classList.add("searching");
  scrim.hidden = panel.hidden = false;
  requestAnimationFrame(() => { scrim.classList.add("is-on"); panel.classList.add("is-on"); });
  query(input.value.trim());
}

export function close() {
  if (!open) return;
  open = false;
  if (abort) abort.abort();
  document.body.classList.remove("searching");
  scrim.classList.remove("is-on");
  panel.classList.remove("is-on");
  input.blur();
  setTimeout(() => {
    if (open) return;
    scrim.hidden = panel.hidden = true;
    list.replaceChildren();
    jumps.replaceChildren();
  }, 320);
}

/* ---------------------------------------------------------------- results */

function fold(text) {
  return (text || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
}

/** Screens and actions matching what was typed. Shown first: reaching a screen must never be buried
    under a film that happens to share a letter. */
function renderJumps(text) {
  const needle = fold(text);
  const found = targets().filter((target) => !needle || fold(target.label).includes(needle));
  jumps.replaceChildren(...found.slice(0, 6).map((target) => h("button", {
    class: "sr-jump",
    onclick: () => {
      close();
      if (target.run) setTimeout(target.run, 60);
      else if (navigate) setTimeout(() => navigate(target.view), 60);
    },
  }, I(target.icon), h("span", { class: "sr-jump-label" }, target.label), h("span", { class: "sr-jump-kind" }, target.kind))));
  return found.length;
}

async function query(text) {
  if (!open) return;
  if (clear) clear.hidden = !text;
  renderJumps(text);

  if (text.length < 2) {
    hint.textContent = "Tape deux lettres : les films de ta bibliothèque, et les écrans par leur nom.";
    list.replaceChildren();
    return;
  }
  if (abort) abort.abort();
  abort = new AbortController();
  const mine = ++run;
  list.replaceChildren(...Array.from({ length: 5 }, () => h("div", { class: "sr-card" }, h("div", { class: "sk sk-poster" }))));
  try {
    const answer = await api(`/api/library/items?q=${encodeURIComponent(text)}&limit=${LIMIT}`, { signal: abort.signal });
    if (mine !== run) return;
    const items = answer.items || [];
    hint.textContent = items.length
      ? `${answer.total} ${answer.total > 1 ? "titres" : "titre"} dans ta bibliothèque`
      : "Rien sous ce nom dans ta bibliothèque.";
    list.replaceChildren(...items.map(card));
  } catch (error) {
    if (mine !== run || error.name === "AbortError") return;
    hint.textContent = error.message;
    list.replaceChildren();
  }
}

function card(item) {
  const art = item.poster
    ? h("img", { src: item.poster, alt: "", loading: "lazy", decoding: "async" })
    : h("div", { class: "sr-blank" }, h("span", {}, (item.title || "?").slice(0, 2).toUpperCase()));
  return h("button", {
    class: `sr-card${item.online ? "" : " offline"}`,
    "aria-label": item.title,
    onclick: () => { const id = item.id; close(); setTimeout(() => openDetail(id), 60); },
  },
  h("div", { class: "sr-art" }, art,
    h("div", { class: "sr-tags" },
      item.quality ? h("span", { class: "sr-tag" }, item.quality) : null,
      (item.langs || []).slice(0, 2).map((code) => h("span", { class: "sr-tag" }, code)))),
  h("div", { class: "sr-name" }, item.title),
  h("div", { class: "sr-sub" }, [item.year, item.kind === "series" ? "Série" : ""].filter(Boolean).join(" · ")));
}
