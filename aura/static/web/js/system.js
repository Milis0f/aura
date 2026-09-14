/* Aura web — system: the TV box, storage, memory and uptime. */
import { h, I, api, emptyState, bytes, stagger } from "./core.js";

let grid;

export function mount(section) {
  grid = h("div", { class: "stats" });
  section.append(
    h("div", { class: "view-head" }, h("div", { class: "grow" },
      h("h1", { class: "view-title" }, "Système"),
      h("div", { class: "view-sub" }, "État du boîtier, de la TV et des disques."))),
    grid);
}

const kv = (key, value) => [h("div", { class: "k" }, key), h("div", {}, value)];

function card({ icon, title, big, sub = "", pct = null, wide = false, extra = null }) {
  const level = pct > 92 ? "crit" : pct > 78 ? "warn" : "";
  return h("div", { class: `stat${wide ? " wide" : ""}` },
    h("h3", {}, I(icon), title),
    h("div", { class: "big" }, big),
    sub ? h("div", { class: "sub" }, sub) : null,
    pct !== null ? h("div", { class: "gauge" }, h("span", { class: level, style: `width:${pct.toFixed(1)}%` })) : null,
    extra);
}

export async function show() {
  if (!grid.children.length) grid.replaceChildren(h("div", { class: "sk sk-block" }), h("div", { class: "sk sk-block" }));
  let stats, sys, player, net;
  try {
    [stats, sys, player, net] = await Promise.all([api("/api/stats"), api("/api/system"), api("/api/player"), api("/api/network")]);
  } catch (error) {
    grid.replaceChildren(emptyState("info", "État indisponible", error.message));
    return;
  }
  const p = player.state || {};
  const playing = p.backend && p.backend !== "idle";
  const cards = [card({
    wide: true, icon: "tv", title: "Boîtier TV",
    big: playing ? (p.name || "Lecture en cours") : "Rien en lecture",
    sub: [player.tv_connected ? "écran connecté" : "écran non connecté", p.backend === "mpv" ? "lecteur mpv" : p.backend === "browser" ? "lecteur intégré" : ""].filter(Boolean).join(" · "),
    extra: h("div", {},
      h("div", { class: "kv" },
        kv("Adresse", net.urls[0] || "—"),
        kv("Réseau", `${net.online ? "en ligne" : "hors ligne"}${net.managed === false ? " · géré par le système" : ""}`),
        kv("Machine", sys.model || sys.platform),
        kv("Version", `Aura ${sys.version}`),
        kv("Lecteurs", `mpv ${sys.mpv ? "présent" : "absent"} · Chrome ${sys.chrome ? "présent" : "absent"}`)),
      h("a", { class: "btn small", href: "/remote/", style: "margin-top:16px" }, I("remote"), "Ouvrir la télécommande")),
  })];
  stats.disks.forEach((d) => cards.push(card({ icon: "drive", title: d.label, big: bytes(d.free), sub: `libres sur ${bytes(d.total)}`, pct: d.total ? (d.used / d.total) * 100 : 0 })));
  if (stats.mem && stats.mem.total) {
    cards.push(card({ icon: "cpu", title: "Mémoire", big: bytes(stats.mem.available), sub: `disponibles sur ${bytes(stats.mem.total)}`, pct: ((stats.mem.total - stats.mem.available) / stats.mem.total) * 100 }));
  }
  if (stats.uptime !== null && stats.uptime !== undefined) {
    const days = Math.floor(stats.uptime / 86400), hours = Math.floor((stats.uptime % 86400) / 3600);
    cards.push(card({ icon: "clock", title: "En service depuis", big: days ? `${days} j ${hours} h` : `${hours} h`, sub: `charge ${stats.load !== null && stats.load !== undefined ? stats.load.toFixed(2) : "—"} · ${stats.sessions} session(s)` }));
  } else {
    cards.push(card({ icon: "devices", title: "Sessions ouvertes", big: String(stats.sessions), sub: "tous comptes confondus" }));
  }
  grid.replaceChildren(...cards.map((c, i) => stagger(c, i)));
}
