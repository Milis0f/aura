/* Aura web — where a download goes.

   Two modes. On the box: qBittorrent writes to a volume Aura knows, and the file joins the library on
   its own. On this machine: the browser saves the .torrent (relayed by the box, because the indexer is
   cross-origin) or hands the magnet to whatever BitTorrent client is installed here.

   The chosen mode and destination are always spelled out before anything starts. */
import { h, I, api, toast, busy, bytes } from "./core.js";
import { flyToDownloads } from "./fly-to-downloads.js";

const MODES = [
  { key: "server", icon: "drive", label: "Serveur", hint: "qBittorrent télécharge sur le boîtier" },
  { key: "local", icon: "monitor", label: "Disque local", hint: "ce navigateur récupère le fichier" },
];

let targets = null;
let pending = null;

/** Volumes the box accepts, fetched once per page load. */
export function serverVolumes(reload = false) {
  if (reload) { targets = null; pending = null; }
  if (targets) return Promise.resolve(targets);
  if (!pending) {
    pending = api("/api/torrents/targets")
      .then((answer) => { targets = answer.targets || []; return targets; })
      .catch((error) => { pending = null; throw error; });
  }
  return pending;
}

export function forget() { targets = null; pending = null; }

/** `value` is { mode, volume, category }; `onChange` receives a new object, never a mutated one. */
export function targetPicker({ value, onChange, release, onStarted }) {
  const el = h("div", { class: "target" });
  const set = (patch) => { onChange({ ...value, ...patch }); };

  const modes = h("div", { class: "target-modes", role: "radiogroup", "aria-label": "Destination du téléchargement" },
    MODES.map((mode) => h("button", {
      type: "button", role: "radio", "aria-checked": String(value.mode === mode.key),
      class: `target-mode${value.mode === mode.key ? " is-on" : ""}`,
      onclick: () => set({ mode: mode.key }),
    }, I(mode.icon), h("span", {}, h("b", {}, mode.label), h("small", {}, mode.hint)))));

  el.append(modes, value.mode === "server" ? serverSide() : localSide());
  return el;

  function serverSide() {
    const category = h("select", { "aria-label": "Dossier", onchange: (event) => set({ category: event.target.value }) },
      h("option", { value: "Films", selected: value.category === "Films" }, "Films"),
      h("option", { value: "Series", selected: value.category === "Series" }, "Séries"));
    const volume = h("select", { "aria-label": "Disque de destination", disabled: true },
      h("option", {}, "Lecture des disques…"));
    const summary = h("p", { class: "target-sum" });
    const go = h("button", {
      class: "btn primary", disabled: !link(),
      onclick: (event) => busy(event.currentTarget, start),
    }, I("download"), "Télécharger ici");

    serverVolumes().then((found) => {
      volume.replaceChildren(...found.map((target) => h("option", {
        value: target.id, selected: target.id === value.volume,
      }, target.free ? `${target.label} — ${bytes(target.free)} libres` : target.label)));
      volume.disabled = false;
      if (!found.some((target) => target.id === value.volume)) volume.value = found.length ? found[0].id : "";
      volume.onchange = () => set({ volume: volume.value });
      describe(found);
    }).catch((error) => {
      volume.replaceChildren(h("option", {}, "Disques indisponibles"));
      summary.textContent = error.message;
    });

    function describe(found) {
      const target = found.find((item) => item.id === volume.value) || found[0];
      const folder = value.category === "Series" ? "Séries" : "Films";
      summary.replaceChildren(I("check"), h("span", {},
        "Sur le boîtier, dans ", h("b", {}, `${target ? target.label : "dossier médias"} › ${folder}`)));
    }

    async function start() {
      await api("/api/torrents/add", { method: "POST", form: { magnet: link(), category: value.category, volume: volume.value || "" } });
      flyToDownloads(el.closest(".sheet") ? el.closest(".dt-body") : el);
      toast("Téléchargement lancé sur le boîtier.", "ok", "download");
      if (onStarted) onStarted();
    }

    return h("div", { class: "target-body" },
      h("div", { class: "target-row" }, volume, category),
      summary,
      h("div", { class: "target-actions" }, go));
  }

  function localSide() {
    const actions = h("div", { class: "target-actions" });
    if (release.torrent_url) {
      actions.append(h("a", {
        class: "btn", download: "", href: `/api/torrents/file/${encodeURIComponent(release.id)}`,
      }, I("download"), "Télécharger le .torrent"));
    }
    if (release.magnet) {
      actions.append(h("a", { class: "btn", href: release.magnet }, I("magnet"), "Ouvrir dans mon client"));
    }
    if (!actions.childElementCount) {
      actions.append(h("span", { class: "dim" }, "Cette source n'expose ni fichier .torrent ni lien magnet."));
    }
    const note = release.magnet && !release.torrent_url
      ? "Un lien magnet ne contient pas le fichier : seul ton client peut le récupérer."
      : "Le fichier arrive dans le dossier de téléchargement de ce navigateur.";
    return h("div", { class: "target-body" },
      h("p", { class: "target-sum" }, I("check"), h("span", {}, "Sur ", h("b", {}, "cette machine"), ". ", note)),
      actions);
  }

  function link() { return release.magnet || release.torrent_url || ""; }
}
