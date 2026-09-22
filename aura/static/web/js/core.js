/* Aura web — shared helpers: DOM, API, options, event bus, toasts, dialogs, formatting, spatial navigation. */

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key === "style") el.setAttribute("style", value);
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
    else if (typeof value !== "string" && key in el) el[key] = value;
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return el;
}

export const I = (name, cls = "") => window.AuraIcons.svg(name, cls);
export const hydrate = (root = document) => window.AuraIcons.hydrate(root);
export const stagger = (el, index) => {
  if (index < 16) { el.classList.add("rise"); el.style.setProperty("--i", index); }
  return el;
};

/* ---------------------------------------------------------------- state, options, events */
export const state = { csrf: "", user: "", canWrite: false, canTorrent: false, has2fa: false, zone: "lan", version: "", jellyfin: "", view: "", player: null };

const DEFAULTS = {
  density: "cosy", aurora: true, tv: false, hidden: false, confirm: true, sort: "name", desc: false,
  grid: false, category: "Films", refresh: 3000, playTarget: "tv", libKind: "all", libSort: "added", libOffline: false,
};
export const opt = (() => {
  try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem("aura.web") || "{}") }; } catch { return { ...DEFAULTS }; }
})();
export function saveOpt() {
  try { localStorage.setItem("aura.web", JSON.stringify(opt)); } catch { /* private browsing: options last for this visit */ }
}
export function applyOpts() {
  const root = document.documentElement;
  root.dataset.density = opt.density;
  document.body.classList.toggle("no-aurora", !opt.aurora);
  document.body.classList.toggle("tv", Boolean(opt.tv));
}

const listeners = new Map();
export const events = {
  on(type, fn) {
    if (!listeners.has(type)) listeners.set(type, new Set());
    listeners.get(type).add(fn);
    return () => listeners.get(type).delete(fn);
  },
  emit(type, payload) {
    for (const fn of listeners.get(type) || []) {
      try { fn(payload); } catch (error) { queueMicrotask(() => { throw error; }); }
    }
  },
};

/* ---------------------------------------------------------------- API */
export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}
const messageOf = (data, status) => {
  const detail = data && (data.detail ?? data.error);
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail[0] && detail[0].msg) return `Données invalides : ${detail[0].msg}`;
  return `Erreur ${status}`;
};
export const toForm = (fields) => {
  const form = new FormData();
  Object.entries(fields).forEach(([k, v]) => form.append(k, v));
  return form;
};

export async function api(path, { method = "GET", json, form, headers = {}, signal } = {}) {
  const init = { method, credentials: "same-origin", headers: { ...headers }, signal };
  if (method !== "GET" && state.csrf) init.headers["X-CSRF-Token"] = state.csrf;
  if (json !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(json);
  } else if (form !== undefined) {
    init.body = form instanceof FormData ? form : toForm(form);
  }
  let response;
  try { response = await fetch(path, init); } catch (error) {
    if (error && error.name === "AbortError") throw error;  // a search the user replaced, not a failure
    throw new ApiError("Le boîtier ne répond pas.", 0);
  }
  const isJson = (response.headers.get("content-type") || "").includes("json");
  const data = isJson ? await response.json().catch(() => null) : null;
  if (response.status === 401 && state.csrf) events.emit("unauthorized");
  if (!response.ok) throw new ApiError(messageOf(data, response.status), response.status);
  return data;
}

/* ---------------------------------------------------------------- feedback */
export function toast(message, kind = "", icon = "") {
  const glyph = icon || (kind === "err" ? "info" : kind === "ok" ? "check" : "");
  const el = h("div", { class: `toast ${kind}`, role: kind === "err" ? "alert" : "status" }, glyph ? I(glyph) : null, h("span", {}, message));
  $("#toasts").append(el);
  setTimeout(() => { el.classList.add("leaving"); setTimeout(() => el.remove(), 320); }, kind === "err" ? 5200 : 3800);
}

export async function busy(button, fn) {
  if (!button || button.classList.contains("is-busy")) return undefined;
  button.classList.add("is-busy");
  button.disabled = true;
  try { return await fn(); } catch (error) { toast(error.message, "err"); return undefined; } finally {
    button.classList.remove("is-busy");
    button.disabled = false;
  }
}

export function dialog({ title, text = "", body = null, ok = "Confirmer", cancel = "Annuler", danger = false }) {
  return new Promise((resolve) => {
    const modal = $("#modal"), form = $("#modalForm"), slot = $("#modalSlot"), okBtn = $("#modalOk"), cancelBtn = $("#modalCancel");
    const previous = document.activeElement;
    $("#modalTitle").textContent = title;
    $("#modalText").textContent = text;
    $("#modalText").hidden = !text;
    okBtn.textContent = ok || "";
    okBtn.hidden = ok === false;
    okBtn.className = `btn ${danger ? "danger" : "primary"}`;
    cancelBtn.textContent = cancel;
    slot.replaceChildren(...(body ? [body] : []));
    hydrate(slot);
    modal.hidden = false;
    setTimeout(() => (slot.querySelector("input, select, textarea") || (ok === false ? cancelBtn : okBtn)).focus(), 40);
    const close = (value) => {
      modal.hidden = true;
      slot.replaceChildren();
      form.removeEventListener("submit", onOk);
      cancelBtn.removeEventListener("click", onCancel);
      modal.removeEventListener("keydown", onKey);
      modal.removeEventListener("click", onBackdrop);
      if (previous && previous.focus) previous.focus();
      resolve(value);
    };
    const onOk = (e) => { e.preventDefault(); close(true); };
    const onCancel = () => close(null);
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); close(null); } };
    const onBackdrop = (e) => { if (e.target === modal) close(null); };
    form.addEventListener("submit", onOk);
    cancelBtn.addEventListener("click", onCancel);
    modal.addEventListener("keydown", onKey);
    modal.addEventListener("click", onBackdrop);
  });
}

export function field(label, attrs = {}, help = "") {
  const input = h(attrs.type === "select" ? "select" : "input", { ...attrs, type: attrs.type === "select" ? null : attrs.type });
  const el = h("label", { class: "field" }, h("span", {}, label), input, help ? h("small", { class: "help" }, help) : null);
  return { el, input };
}

export const emptyState = (icon, title, text, { actions = [], steps = [] } = {}) =>
  h("div", { class: "empty-state rise" }, h("div", { class: "es-icon" }, I(icon)),
    h("div", {}, h("div", { class: "es-title" }, title), h("div", { class: "es-text" }, text),
      steps.length ? h("ol", { class: "es-steps" }, steps.map((s) => h("li", {}, s))) : null,
      actions.length ? h("div", { class: "es-actions" }, actions) : null));

/* ---------------------------------------------------------------- formatting */
export function bytes(n) {
  if (!n) return "0 o";
  const units = ["o", "Ko", "Mo", "Go", "To"];
  const i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), units.length - 1);
  const v = n / 1024 ** i;
  return `${String(v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)).replace(".", ",")} ${units[i]}`;
}
export const speed = (n) => (n / 1048576).toFixed(1).replace(".", ",");
export function when(ts) {
  const d = new Date(ts * 1000), days = (Date.now() - d) / 86400000;
  if (days < 1) return d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  if (days < 340) return d.toLocaleDateString("fr-FR", { day: "numeric", month: "short" });
  return d.toLocaleDateString("fr-FR", { day: "numeric", month: "short", year: "numeric" });
}
export function ago(ts) {
  if (!ts) return "jamais";
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "à l'instant";
  if (s < 3600) return `il y a ${Math.round(s / 60)} min`;
  if (s < 86400) return `il y a ${Math.round(s / 3600)} h`;
  return `il y a ${Math.round(s / 86400)} j`;
}
export function eta(s) {
  if (!s || s >= 8640000) return "—";
  const hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60);
  if (hh) return `${hh} h ${String(mm).padStart(2, "0")}`;
  return mm ? `${mm} min` : `${s} s`;
}
export function duration(seconds) {
  const m = Math.round((seconds || 0) / 60);
  if (!m) return "";
  return m >= 60 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")}` : `${m} min`;
}
export function clock(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  const hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60;
  return `${hh ? `${hh}:` : ""}${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
}
export const episodeCode = (season, episode) => `S${String(season).padStart(2, "0")}E${String(episode).padStart(2, "0")}`;
export function debounce(fn, ms) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

/* ---------------------------------------------------------------- spatial navigation (TV mode, arrows) */
const FOCUSABLE = "button:not([disabled]), [href], input:not([type=hidden]), select, textarea, [tabindex]:not([tabindex='-1'])";
const LAYERS = ["#modal", "#viewer", "#detail", "#settings"];

export function topLayer() {
  return LAYERS.map((sel) => $(sel)).find((el) => el && !el.hidden) || $("#app");
}

export function spatialMove(direction) {
  const layer = topLayer();
  const items = $$(FOCUSABLE, layer).filter((el) => el.offsetParent !== null || el === document.activeElement);
  const current = document.activeElement;
  if (!current || !items.includes(current)) { if (items[0]) items[0].focus(); return; }
  const a = current.getBoundingClientRect();
  const ax = a.left + a.width / 2, ay = a.top + a.height / 2;
  let best = null, score = Infinity;
  for (const el of items) {
    if (el === current) continue;
    const b = el.getBoundingClientRect();
    const dx = b.left + b.width / 2 - ax, dy = b.top + b.height / 2 - ay;
    const forward = { left: -dx, right: dx, up: -dy, down: dy }[direction];
    if (forward <= 4) continue;
    const lateral = direction === "left" || direction === "right" ? Math.abs(dy) : Math.abs(dx);
    const s = forward + lateral * 2.2;
    if (s < score) { score = s; best = el; }
  }
  if (best) { best.focus(); best.scrollIntoView({ block: "nearest", inline: "nearest" }); }
}
