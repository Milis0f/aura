/* Aura web — drives: what is plugged in, what the library found on it, safe removal, watched folders. */
import { h, I, api, state, events, toast, dialog, busy, emptyState, stagger, bytes, ago } from "./core.js";

let head, list, folders;
let pollTimer = null;

export function mount(section) {
  head = h("div", { class: "view-head" });
  list = h("div", { class: "drive-list" });
  folders = h("section", { class: "folders" });
  section.append(head, list, folders);
}

export async function show() {
  await load();
  if (state.canWrite) loadFolders(); else folders.replaceChildren();
}

const id = (d) => encodeURIComponent(d.id);
const plural = (n, word) => `${n} ${word}${n > 1 ? "s" : ""}`;

async function load() {
  if (!list.children.length) list.replaceChildren(h("div", { class: "sk sk-block" }), h("div", { class: "sk sk-block" }));
  let data;
  try { data = await api("/api/drives"); } catch (error) {
    list.replaceChildren(emptyState("info", "Disques indisponibles", error.message));
    return;
  }
  const drives = data.drives;
  const plugged = drives.filter((d) => d.available).length;
  const unplugged = drives.length - plugged;
  head.replaceChildren(
    h("div", { class: "grow" },
      h("h1", { class: "view-title" }, "Disques"),
      h("div", { class: "view-sub" }, [
        plural(plugged, "branché"), unplugged ? plural(unplugged, "débranché") : "",
        data.automount ? "montage automatique activé" : "montage automatique désactivé",
      ].filter(Boolean).join(" · "))),
    h("button", { class: "btn", onclick: (e) => busy(e.currentTarget, async () => {
      const result = await api("/api/library/scan", { method: "POST" });
      toast(result.scheduled ? "Analyse lancée." : "Une analyse est déjà en cours.", "ok", "scan");
      setTimeout(load, 600);
    }) }, I("scan"), "Tout analyser"));
  if (!drives.length) {
    list.replaceChildren(emptyState("usb", "Aucun disque pour l'instant",
      "Branche un disque dur ou une clé USB sur le boîtier : Aura le monte, cherche les films et les séries, puis te prévient.",
      { steps: ["NTFS (Windows), exFAT, FAT32, ext4 et HFS+ se lisent directement.", "Un disque déjà monté par OpenMediaVault est repris tel quel.", "Tu peux aussi suivre un dossier précis, juste en dessous."] }));
  } else {
    list.replaceChildren(...drives.map((d, i) => stagger(driveCard(d, data.can_mount), i)));
  }
  clearTimeout(pollTimer);
  if (drives.some((d) => d.scanning) && state.view === "drives") pollTimer = setTimeout(load, 2000);
}

function statusOf(d) {
  if (d.scanning) return ["scan", `Analyse en cours${d.scanned_files ? ` · ${d.scanned_files.toLocaleString("fr-FR")} vidéos vues` : ""}`];
  if (!d.available) return ["eject", `Débranché${d.last_seen ? ` · vu ${ago(d.last_seen)}` : ""}`];
  if (!d.readable) return ["info", `Format ${d.fstype || "inconnu"} non lisible sous Linux`];
  if (!d.mounted) return ["info", "Branché mais pas monté"];
  if (d.scan_state && d.scan_state.startsWith("erreur")) return ["info", d.scan_state];
  if (d.last_scan) return ["film", `${plural(d.films, "film")} · ${plural(d.episodes, "épisode")} · analysé ${ago(d.last_scan)}`];
  return ["clock", "En attente d'analyse"];
}

function driveCard(d, canMount) {
  const icon = d.kind === "folder" ? "folder" : d.transport === "usb" || d.removable ? "usb" : "drive";
  const usage = d.usage || {};
  const used = usage.total ? (usage.used / usage.total) * 100 : 0;
  const level = used > 92 ? "crit" : used > 78 ? "warn" : "";
  const [statusIcon, statusText] = statusOf(d);
  const sub = d.kind === "folder" ? d.mountpoint : [d.fstype ? d.fstype.toUpperCase() : "", d.model, d.size ? bytes(d.size) : ""].filter(Boolean).join(" · ");
  const actions = h("div", { class: "drive-actions" });
  if (d.films || d.episodes) {
    actions.append(h("button", { class: "btn small", onclick: () => events.emit("navigate", { view: "library", arg: { drive: d.id } }) }, I("film"), "Voir les titres"));
  }
  if (d.available && d.mounted) {
    actions.append(h("button", { class: "btn small ghost", onclick: () => events.emit("navigate", { view: "files", arg: { root: d.label } }) }, I("folder"), "Parcourir"));
  }
  if (d.in_library && !d.scanning) {
    actions.append(h("button", { class: "btn small ghost", onclick: (e) => busy(e.currentTarget, async () => {
      await api(`/api/drives/${id(d)}/scan`, { method: "POST" });
      toast(`Analyse de « ${d.label} » lancée.`, "ok", "scan");
      setTimeout(load, 500);
    }) }, I("scan"), "Analyser"));
  }
  if (d.kind === "disk" && d.available && !d.mounted && d.readable && canMount) {
    actions.append(h("button", { class: "btn small", onclick: (e) => busy(e.currentTarget, async () => {
      await api(`/api/drives/${id(d)}/mount`, { method: "POST" });
      toast(`« ${d.label} » monté.`, "ok", "drive");
      setTimeout(load, 900);
    }) }, I("drive"), "Monter"));
  }
  if (d.kind === "disk" && d.mounted && d.removable && !d.managed && canMount) {
    actions.append(h("button", { class: "btn small ghost", onclick: () => eject(d) }, I("eject"), "Éjecter"));
  }
  if (!d.available) actions.append(h("button", { class: "btn small ghost danger", onclick: () => forget(d) }, I("trash"), "Oublier"));
  return h("article", { class: `drive${d.available ? "" : " offline"}` },
    h("div", { class: "drive-ic" }, I(icon)),
    h("div", { class: "grow" },
      h("div", { class: "drive-name" }, d.label,
        d.managed === "omv" ? h("span", { class: "tag" }, "OMV") : null,
        d.readonly ? h("span", { class: "tag warn" }, "lecture seule") : null),
      h("div", { class: "drive-sub" }, sub),
      d.scanning
        ? h("div", { class: "gauge indeterminate" }, h("span"))
        : usage.total ? h("div", { class: "gauge", title: `${bytes(usage.free)} libres sur ${bytes(usage.total)}` }, h("span", { class: level, style: `width:${used.toFixed(1)}%` })) : null,
      h("div", { class: "drive-status" }, I(statusIcon), h("span", {}, statusText), usage.total ? h("span", { class: "dim num" }, `· ${bytes(usage.free)} libres`) : null)),
    actions);
}

async function eject(d) {
  if (!await dialog({ title: `Éjecter « ${d.label} »`, text: "Une lecture en cours depuis ce disque s'arrête. Attends le message avant de débrancher.", ok: "Éjecter" })) return;
  try {
    const result = await api(`/api/drives/${id(d)}/eject`, { method: "POST" });
    toast(result.message || "Tu peux débrancher le disque.", "ok", "eject");
    setTimeout(load, 900);
  } catch (error) { toast(error.message, "err"); }
}

async function forget(d) {
  if (!await dialog({ title: `Oublier « ${d.label} »`, text: "Ses titres disparaissent de la bibliothèque. Les points de reprise restent si tu le rebranches.", ok: "Oublier", danger: true })) return;
  try {
    await api(`/api/drives/${id(d)}`, { method: "DELETE" });
    toast("Disque oublié.", "ok");
    load();
  } catch (error) { toast(error.message, "err"); }
}

async function loadFolders() {
  let data;
  try { data = await api("/api/library/folders"); } catch { folders.replaceChildren(); return; }
  const path = h("input", { placeholder: "/srv/dev-disk-by-uuid-…/Media/Films", autocomplete: "off", spellcheck: "false" });
  const label = h("input", { placeholder: "Nom affiché (facultatif)", autocomplete: "off", style: "flex:0 1 220px" });
  const submit = h("button", { class: "btn", type: "submit" }, I("folder-plus"), "Suivre");
  const row = (f, removable) => h("div", { class: "folder-row" }, I(removable ? "folder" : "lock"),
    h("div", { class: "grow" }, h("div", {}, f.label), h("div", { class: "path" }, f.path)),
    removable
      ? h("button", { class: "icon-btn danger", title: "Ne plus suivre", "aria-label": `Ne plus suivre ${f.label}`, onclick: (e) => busy(e.currentTarget, async () => {
        await api(`/api/library/folders?path=${encodeURIComponent(f.path)}`, { method: "DELETE" });
        toast("Dossier retiré.", "ok");
        setTimeout(show, 600);
      }) }, I("trash"))
      : h("span", { class: "tag" }, "configuration"));
  folders.replaceChildren(
    h("h2", { class: "section-title" }, "Dossiers suivis", h("span", { class: "count" }, String(data.folders.length + data.configured.length))),
    h("p", { class: "view-sub", style: "margin:-4px 0 12px" }, "Un dossier du boîtier ajouté aux fichiers et à la bibliothèque, en plus des disques branchés."),
    ...data.configured.map((f) => row(f, false)),
    ...data.folders.map((f) => row(f, true)),
    h("form", { class: "add-folder", onsubmit: (e) => {
      e.preventDefault();
      busy(submit, async () => {
        await api("/api/library/folders", { method: "POST", json: { path: path.value.trim(), label: label.value.trim() } });
        toast("Dossier suivi : l'analyse démarre.", "ok", "folder");
        path.value = "";
        label.value = "";
        setTimeout(show, 1200);
      });
    } }, path, label, submit));
}

export function onEvent(message) {
  if (!list || state.view !== "drives") return;
  if (["drive_added", "drive_removed", "library_scan", "library_changed"].includes(message.type)) {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(load, 500);
  }
}
