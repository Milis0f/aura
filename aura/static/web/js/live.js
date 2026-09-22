/* Aura web — Direct: the channels the box knows, grouped as the playlist declares them.

   This lived only on /tv/ until now, which is why the box felt like two products sharing a server.
   The data and the endpoints were always shared; only the interface was duplicated. */
import { $, h, I, api, opt, state, toast, busy, emptyState, stagger, debounce } from "./core.js";
import * as viewer from "./viewer.js";

const PAGE = 90;
const live = { group: "", q: "", groups: [], offset: 0, total: 0 };
let groupsBox, grid, moreBox, head;
let token = 0;

export function mount(section) {
  head = h("div", { class: "view-head" }, h("div", { class: "grow" },
    h("h1", { class: "view-title" }, "Direct"),
    h("div", { class: "view-sub" }, "Les chaînes de tes sources, en direct.")));
  groupsBox = h("div", { class: "cat-chips" });
  grid = h("div", { class: "chan-grid" });
  moreBox = h("div", { class: "lib-more" });
  section.append(head, groupsBox, grid, moreBox);
}

export async function show() {
  await load(true);
}

export const query = () => live.q;

export function search(q) {
  live.q = q;
  load(true);
}

function renderGroups() {
  const pick = (name) => { live.group = live.group === name ? "" : name; renderGroups(); load(true); };
  groupsBox.replaceChildren(
    h("button", { class: `chip${live.group ? "" : " is-active"}`, onclick: () => pick("") },
      "Toutes", h("span", { class: "chip-n" }, String(live.total || ""))),
    ...live.groups.map((g) => h("button", {
      class: `chip${live.group === g.name ? " is-active" : ""}`, onclick: () => pick(g.name),
    }, g.name, h("span", { class: "chip-n" }, String(g.count)))));
}

async function load(reset) {
  const mine = ++token;
  if (reset) {
    live.offset = 0;
    grid.replaceChildren(...Array.from({ length: 12 }, () => h("div", { class: "sk sk-chan" })));
    moreBox.replaceChildren();
  }
  const params = new URLSearchParams({ kind: "live", limit: String(PAGE), offset: String(live.offset) });
  if (live.group) params.set("group", live.group);
  if (live.q) params.set("q", live.q);
  let data;
  try { data = await api(`/api/channels?${params}`); } catch (error) {
    if (mine === token) grid.replaceChildren(emptyState("info", "Chaînes indisponibles", error.message));
    return;
  }
  if (mine !== token) return;

  if (data.groups && data.groups.length) {
    live.groups = data.groups;
    live.total = data.groups.reduce((sum, g) => sum + (g.count || 0), 0);
    renderGroups();
  }
  if (reset) grid.replaceChildren();
  if (!data.items.length) {
    grid.replaceChildren(emptyState("signal", "Aucune chaîne",
      live.q ? "Essaie un autre mot." : "Ajoute une source dans Système pour remplir le direct."));
    return;
  }
  data.items.forEach((channel, i) => grid.append(stagger(card(channel), reset ? i : 0)));
  live.offset += data.items.length;
  // The endpoint reports no total, so a full page is the only honest signal that more exists.
  moreBox.replaceChildren(data.items.length === PAGE
    ? h("button", { class: "btn", onclick: (e) => busy(e.currentTarget, () => load(false)) }, "Afficher plus")
    : "");
}

function card(channel) {
  const art = channel.logo
    ? h("img", { src: channel.logo, alt: "", loading: "lazy", decoding: "async" })
    : h("span", { class: "chan-initials" }, (channel.name || "?").slice(0, 2).toUpperCase());
  return h("button", {
    class: "chan", "aria-label": channel.name,
    onclick: (event) => busy(event.currentTarget, () => play(channel)),
  },
  h("div", { class: "chan-logo" }, art),
  h("div", { class: "chan-name", title: channel.name }, channel.name),
  channel.group_name ? h("div", { class: "chan-group" }, channel.group_name) : null);
}

async function play(channel) {
  // Same choice as the library: where it plays is a setting, not a question asked every time.
  if (opt.playTarget === "here" && channel.url) {
    viewer.playVideo({ src: channel.url, title: channel.name, subtitle: channel.group_name || "Direct" });
    return;
  }
  await api("/api/play", { method: "POST", json: { channel_id: channel.id, name: channel.name, kind: "live" } });
  toast(`${channel.name} sur la TV.`, "ok", "tv");
}
