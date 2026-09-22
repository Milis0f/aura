/* Aura web — search takes the screen.

   The field leaves the top bar, settles in the middle, and everything behind it falls away under a blur.
   Results are posters, right under the field, with the language badges the file names give us.

   The move is a FLIP: the field is measured where it sits, switched to fixed at those exact coordinates,
   and only then handed to CSS for the destination — so the browser animates a real transition instead of
   teleporting the element. A ghost holds its place in the top bar until it comes back. */
import { $, h, I, api, debounce } from "./core.js";
import { openDetail } from "./library.js";

const LIMIT = 14;
const SETTLE_MS = 460;

let wrap, input, ghost, scrim, panel, list, hint;
let open = false;
let run = 0;
let abort = null;

export function init() {
  wrap = $(".searchwrap");
  input = $("#search");
  if (!wrap || !input) return;

  scrim = h("div", { class: "search-scrim", hidden: true, onclick: close });
  list = h("div", { class: "sr-row" });
  hint = h("p", { class: "sr-hint" });
  panel = h("div", { class: "search-panel", hidden: true }, hint, list);
  document.body.append(scrim, panel);

  input.addEventListener("focus", show);
  input.addEventListener("input", debounce(() => query(input.value.trim()), 260));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && open) { event.stopPropagation(); close(); }
  });
}

export const isOpen = () => open;

function show() {
  if (open) return;
  open = true;
  const box = wrap.getBoundingClientRect();
  ghost = h("div", { class: "searchwrap-ghost", style: `width:${box.width}px;height:${box.height}px` });
  wrap.after(ghost);
  wrap.style.cssText = `position:fixed;top:${box.top}px;left:${box.left}px;width:${box.width}px;margin:0`;
  document.body.append(wrap);
  input.focus(); // re-parenting the field drops the caret, and the whole point is that you keep typing
  document.body.classList.add("searching");
  scrim.hidden = false;
  panel.hidden = false;
  requestAnimationFrame(() => {
    scrim.classList.add("is-on");
    panel.classList.add("is-on");
    // Cleared, not overwritten: the stylesheet owns the destination.
    wrap.style.top = wrap.style.left = wrap.style.width = "";
  });
  query(input.value.trim());
}

function close() {
  if (!open) return;
  open = false;
  if (abort) abort.abort();
  const box = ghost.getBoundingClientRect();
  wrap.style.transform = "none";
  wrap.style.top = `${box.top}px`;
  wrap.style.left = `${box.left}px`;
  wrap.style.width = `${box.width}px`;
  scrim.classList.remove("is-on");
  panel.classList.remove("is-on");
  document.body.classList.remove("searching");
  input.blur();

  let settled = false;
  const settle = () => {
    if (settled) return;
    settled = true;
    if (ghost) { ghost.replaceWith(wrap); ghost = null; }
    wrap.removeAttribute("style");
    scrim.hidden = true;
    panel.hidden = true;
    list.replaceChildren();
  };
  wrap.addEventListener("transitionend", settle, { once: true });
  setTimeout(settle, SETTLE_MS); // a dropped transitionend must not strand the field on top of the page
}

async function query(text) {
  if (!open) return;
  if (text.length < 2) {
    hint.textContent = "Tape deux lettres. La bibliothèque répond pendant que tu écris.";
    list.replaceChildren();
    return;
  }
  if (abort) abort.abort();
  abort = new AbortController();
  const mine = ++run;
  list.replaceChildren(...Array.from({ length: 6 }, () => h("div", { class: "sr-card" }, h("div", { class: "sk sk-poster" }))));
  try {
    const answer = await api(`/api/library/items?q=${encodeURIComponent(text)}&limit=${LIMIT}`, { signal: abort.signal });
    if (mine !== run) return;
    const items = answer.items || [];
    hint.textContent = items.length
      ? `${answer.total} ${answer.total > 1 ? "titres" : "titre"} dans ta bibliothèque`
      : "Rien sous ce nom dans ta bibliothèque. Les téléchargements cherchent plus loin.";
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
      (item.langs || []).slice(0, 2).map((code) => h("span", { class: "sr-tag" }, code)),
      item.online ? null : h("span", { class: "sr-tag" }, I("drive")))),
  h("div", { class: "sr-name" }, item.title),
  h("div", { class: "sr-sub" }, [item.year, item.kind === "series" ? "Série" : ""].filter(Boolean).join(" · ")));
}
