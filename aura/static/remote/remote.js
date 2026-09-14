/* Aura — phone remote & configuration UI. */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const h = (tag, attrs = {}, ...children) => {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") el.className = v;
      else if (k === "html") el.innerHTML = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (v !== null && v !== undefined && v !== false) el.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) { if (c === null || c === undefined || c === false) continue; el.append(c.nodeType ? c : document.createTextNode(String(c))); }
    return el;
  };
  const api = async (path, opts = {}) => {
    const isForm = opts.body instanceof FormData;
    const r = await fetch("/api" + path, { headers: isForm ? {} : { "Content-Type": "application/json" }, ...opts, body: opts.body && !isForm && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body });
    if (!r.ok) { let msg = r.statusText; try { msg = (await r.json()).detail || msg; } catch (_) { /* */ } throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg)); }
    return r.json();
  };
  const post = (path, body) => api(path, { method: "POST", body });
  const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  const fmtDate = (ts) => new Date(ts * 1000).toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", month: "short" });
  const fmtDur = (s) => { s = Math.max(0, Math.floor(s || 0)); const hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60; return (hh ? hh + ":" : "") + String(mm).padStart(2, "0") + ":" + String(ss).padStart(2, "0"); };
  const initials = (n) => (n || "?").replace(/[^A-Za-z0-9 ]/g, "").split(" ").filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";
  const ago = (ts) => { ts = Number(ts); if (!ts) return "jamais"; const m = Math.round((Date.now() / 1000 - ts) / 60); return m < 60 ? `il y a ${m} min` : m < 1440 ? `il y a ${Math.round(m / 60)} h` : `il y a ${Math.round(m / 1440)} j`; };
  let toastTimer;
  const toast = (msg, err = false) => { const t = $("#toast"); t.textContent = msg; t.classList.toggle("error", err); t.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), 3200); };
  const I = (name, cls) => window.AuraIcons.svg(name, cls);
  // Busy state keeps the button's icon and label, and reports failures under the form when there is a slot.
  const busy = async (btn, fn) => {
    const kids = [...btn.childNodes];
    const error = btn.parentElement ? btn.parentElement.querySelector(":scope > .form-error") : null;
    btn.disabled = true; btn.replaceChildren("…"); if (error) error.textContent = "";
    try { return await fn(); } catch (e) { if (error) error.textContent = e.message; else toast(e.message, true); } finally { btn.disabled = false; btn.replaceChildren(...kids); }
  };
  const SPORTS = { f1: ["flag", "F1"], ufc: ["octagon", "UFC"], boxing: ["glove", "Boxe"], football: ["ball", "Foot"], basketball: ["basket", "Basket"], other: ["trophy", "Sport"] };
  const sportIcon = (sport) => (SPORTS[sport] || SPORTS.other)[0];
  const sportLabel = (sport) => (SPORTS[sport] || SPORTS.other)[1];
  const groupLabel = (g) => (!g || /^undefined$/i.test(g) ? "Autres" : g);
  const rating = (r) => h("span", { class: "rating" }, I("star-fill"), Number(r).toFixed(1));
  const emptyState = (icon, title, text, ...actions) => h("div", { class: "empty-state" }, h("div", { class: "es-icon" }, I(icon)), h("div", {}, h("div", { class: "es-title" }, title), h("div", { class: "es-text" }, text), actions.filter(Boolean).length ? h("div", { class: "es-actions" }, ...actions) : null));
  const field = (label, input, help) => h("div", { class: "field" }, h("label", {}, label), input, help ? h("div", { class: "help" }, help) : null);
  const fitLogo = (img, box) => img.addEventListener("load", () => { if (img.naturalWidth && img.naturalWidth / img.naturalHeight > 0.9) box.classList.add("fit"); });
  const skel = {
    lines: (n) => h("div", {}, Array.from({ length: n }, () => h("div", { class: "sk sk-line" }))),
    posters: () => h("div", {}, h("div", { class: "sk sk-hero" }), h("div", { class: "sk-posters" }, Array.from({ length: 6 }, () => h("div", { class: "sk sk-poster" })))),
  };

  const main = $("#main");
  const state = { tab: "remote", player: {}, tvView: "", tvFocus: "", ws: null, browse: { section: "live", group: null, q: "", sport: "all" } };

  // ---------------------------------------------------------------- sheet
  const sheet = (build) => { const s = $("#sheet"); const b = $("#sheet-body"); b.replaceChildren(); build(b); s.hidden = false; };
  const closeSheet = () => { $("#sheet").hidden = true; };
  $("#sheet").addEventListener("click", (e) => { if (e.target === $("#sheet")) closeSheet(); });

  // ---------------------------------------------------------------- tabs
  $$(".tab").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  const showTab = (tab) => { state.tab = tab; $$(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab)); tabs[tab](); };

  // ---------------------------------------------------------------- remote
  const key = (k, text) => post("/remote/key", { key: k, text: text || "" }).catch((e) => toast(e.message, true));
  const nowCard = () => {
    const p = state.player;
    const c = h("div", { class: "card", id: "now" });
    if (!p.backend || p.backend === "idle") { c.append(h("div", { class: "idle" }, I("tv"), "Rien en lecture"), h("div", { class: "muted small", id: "tvview" }, state.tvView ? `TV : ${state.tvView}${state.tvFocus ? " › " + state.tvFocus : ""}` : "")); return c; }
    const pct = p.duration ? (p.position / p.duration) * 100 : 0;
    c.append(h("div", { class: "now" }, h("div", { class: "grow" }, h("div", { class: "t" }, p.backend === "app" ? "App : " + p.name : p.name), h("div", { class: "s" }, p.backend === "app" ? "Trackpad et clavier actifs" : p.kind === "live" ? "Direct" + (p.backend === "mpv" ? " · mpv" : "") : (p.duration ? `${fmtDur(p.position)} / ${fmtDur(p.duration)}` : "Vidéo") + (p.paused ? " · pause" : ""))),
      h("button", { class: "btn", onclick: () => (p.backend === "app" ? post("/apps-home") : post("/stop")).then(refreshPlayer) }, I("stop"))));
    if (p.duration) c.append(h("div", { class: "bar" }, h("div", { class: "bar-fill", style: `width:${pct}%` })));
    if (p.error) c.append(h("div", { class: "small status-err" }, p.error));
    return c;
  };
  const refreshPlayer = async () => { try { const r = await api("/player"); state.player = r.state; const old = $("#now"); if (old) old.replaceWith(nowCard()); } catch (_) { /* */ } };

  const tabs = {
    remote() {
      const v = h("div");
      v.append(nowCard());
      const seg = h("div", { class: "seg" });
      const modes = { dpad: "D-pad", pad: "Trackpad", kbd: "Clavier" };
      const area = h("div");
      const setMode = (m) => { $$("button", seg).forEach((b) => b.classList.toggle("on", b.dataset.m === m)); area.replaceChildren(modeViews[m]()); localStorage.setItem("mode", m); };
      Object.entries(modes).forEach(([k, l]) => seg.append(h("button", { "data-m": k, onclick: () => setMode(k) }, I({ dpad: "dpad", pad: "cursor", kbd: "keyboard" }[k]), l)));
      v.append(seg, area);
      const k = (icon, label, fn) => h("button", { onclick: fn }, I(icon), label);
      v.append(h("div", { class: "keys" },
        k("back", "Retour", () => key("BrowserBack")), k("home", "Accueil", () => post("/remote/navigate/home").then(refreshPlayer)),
        k("pause", "Pause", () => post("/player/pause").then(refreshPlayer)), k("stop", "Stop", () => post("/stop").then(refreshPlayer)),
        k("vol-down", "Moins", () => post("/remote/volume", { delta: -5 })), k("vol-up", "Plus", () => post("/remote/volume", { delta: 5 })),
        k("mute", "Muet", () => post("/remote/volume", { mute: true })), k("info", "Infos", () => key("i"))));
      v.append(h("h2", {}, "Aller à"), h("div", { class: "keys views" }, ["home", "live", "sports", "f1", "ufc", "movies", "series", "apps"].map((vw) => h("button", { onclick: () => post("/remote/navigate/" + vw) }, { home: "Accueil", live: "Direct", sports: "Sports", f1: "F1", ufc: "UFC", movies: "Films", series: "Séries", apps: "Apps" }[vw]))));
      main.replaceChildren(v);
      setMode(localStorage.getItem("mode") || "dpad");
      refreshPlayer();
    },
    browse() { renderBrowse(); },
    settings() { renderSettings(); },
  };

  const modeViews = {
    dpad: () => h("div", { class: "dpad" },
      h("button", { class: "void" }), h("button", { onclick: () => key("ArrowUp") }, I("chevron-up")), h("button", { class: "void" }),
      h("button", { onclick: () => key("ArrowLeft") }, I("chevron-left")), h("button", { class: "ok", onclick: () => key("Enter") }, "OK"), h("button", { onclick: () => key("ArrowRight") }, I("chevron-right")),
      h("button", { class: "void" }), h("button", { onclick: () => key("ArrowDown") }, I("chevron-down")), h("button", { class: "void" })),
    pad: () => {
      const pad = h("div", { class: "pad" }, h("div", {}, I("cursor"), "Glisse pour déplacer le curseur, touche pour cliquer, deux doigts pour défiler"));
      let last = null, moved = 0, t0 = 0, acc = { dx: 0, dy: 0 }, flush = null, fingers = 0;
      const send = () => { if (!acc.dx && !acc.dy) return; post("/remote/pointer", { dx: acc.dx, dy: acc.dy }).catch(() => {}); acc = { dx: 0, dy: 0 }; };
      pad.addEventListener("touchstart", (e) => { fingers = e.touches.length; last = { x: e.touches[0].clientX, y: e.touches[0].clientY }; moved = 0; t0 = Date.now(); pad.classList.add("active"); e.preventDefault(); }, { passive: false });
      pad.addEventListener("touchmove", (e) => {
        const t = e.touches[0]; const dx = t.clientX - last.x, dy = t.clientY - last.y; last = { x: t.clientX, y: t.clientY }; moved += Math.abs(dx) + Math.abs(dy);
        if (e.touches.length >= 2) { post("/remote/pointer", { scroll: -dy * 3 }).catch(() => {}); }
        else { acc.dx += dx * 2.2; acc.dy += dy * 2.2; if (!flush) flush = setTimeout(() => { flush = null; send(); }, 40); }
        e.preventDefault();
      }, { passive: false });
      pad.addEventListener("touchend", (e) => { pad.classList.remove("active"); send(); if (moved < 8 && Date.now() - t0 < 350) post("/remote/pointer", { click: fingers >= 2 ? "right" : "left" }).catch(() => {}); e.preventDefault(); }, { passive: false });
      return h("div", {}, pad, h("div", { class: "keys", style: "grid-template-columns:repeat(3,1fr)" }, h("button", { onclick: () => post("/remote/pointer", { click: "left" }) }, "Clic"), h("button", { onclick: () => key("Escape") }, "Échap"), h("button", { onclick: () => key("F11") }, "Plein écran")));
    },
    kbd: () => {
      const input = h("input", { class: "input", placeholder: "Texte à envoyer à la TV…", autocomplete: "off" });
      const send = () => { const t = input.value; if (!t) return; post("/remote/text", { text: t }).then(() => { input.value = ""; }).catch((e) => toast(e.message, true)); };
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") { send(); key("Enter"); } });
      return h("div", {}, h("div", { class: "kbd-row" }, input, h("button", { class: "btn primary", onclick: send }, "Envoyer")),
        h("div", { class: "keys", style: "margin-top:8px" }, h("button", { onclick: () => key("Backspace") }, I("backspace"), "Effacer"), h("button", { onclick: () => key("Tab") }, I("arrow-right"), "Tab"), h("button", { onclick: () => key("Enter") }, I("enter"), "Entrée"), h("button", { onclick: () => key("Space", " ") }, I("space"), "Espace")),
        h("div", { class: "muted small" }, "Sur Aura : remplit le champ actif ou lance une recherche. Dans une app (Netflix…) : tape dans le champ sélectionné."));
    },
  };

  // ---------------------------------------------------------------- browse
  const playOnTv = async (item, extra = {}) => { try { await post("/play", { channel_id: item.id || "", url: item.id ? "" : item.url, name: item.name, kind: item.kind || "live", ...extra }); toast(item.name + " sur la TV"); refreshPlayer(); } catch (e) { toast(e.message, true); } };
  const favBtn = (c) => { const b = h("button", { class: "act" + (c.favorite ? " on" : ""), onclick: async (e) => { e.stopPropagation(); try { const r = await post("/favorites/" + c.id); c.favorite = r.favorite; b.classList.toggle("on", r.favorite); b.replaceChildren(I(r.favorite ? "star-fill" : "star")); } catch (err) { toast(err.message, true); } } }, I(c.favorite ? "star-fill" : "star")); return b; };
  const chanItem = (c) => h("button", { class: "item", onclick: () => (c.kind === "series" ? openSeries(c) : c.kind === "vod" ? openVod(c) : playOnTv(c)) },
    c.logo ? h("img", { class: "logo", src: c.logo, alt: "", loading: "lazy", onerror: (e) => e.target.replaceWith(h("div", { class: "ph" }, initials(c.name))) }) : h("div", { class: "ph" }, initials(c.name)),
    h("div", { class: "grow" }, h("div", { class: "n" }, c.name), h("div", { class: "s" }, c.now ? c.now.title + (c.next ? " · puis " + c.next.title : "") : c.group || "")), favBtn(c));
  const cleanTitle = (n) => {
    let t = (n || "").trim().replace(/\.(mkv|mp4|avi|mov|m4v|webm|ts|wmv|flv)$/i, "");
    const release = (t.match(/\./g) || []).length >= 2 || t.includes("_");
    t = t.replace(/\b[\w-]+\.(com|net|org|tv|cc|me|io|to|xyz)\b/gi, " ");
    if (release) t = t.replace(/[._]/g, " ").replace(/\s*-\s*[A-Za-z0-9]+\s*$/, "");
    t = t.replace(/^\s*[A-Z]{2,3}\s*[-|:]\s*/, "");
    t = t.replace(/\b(FR|VF|VFF|VFQ|VO|VOST|VOSTFR|MULTI|TRUEFRENCH|FRENCH|2160p|1080p|1080i|720p|480p|4K|UHD|HD|SD|HDR|HDR10|BLURAY|BRRIP|BDRIP|WEBRIP|WEB-DL|WEBDL|HDRIP|DVDRIP|HDTV|x264|x265|H264|H265|HEVC|AVC|XVID|AAC|AC3|DTS|10BIT|REMUX|PROPER|REPACK)\b/gi, " ");
    return t.replace(/[\[\]{}|]+/g, " ").replace(/\(\s*\)/g, "").replace(/\s+/g, " ").replace(/\s*[-:]\s*$/, "").trim();
  };
  const posterItem = (c) => {
    const box = h("div", { class: "img" });
    if (c.logo) { const img = h("img", { src: c.logo, alt: "", loading: "lazy", onerror: (e) => e.target.replaceWith(initials(c.name)) }); fitLogo(img, box); box.append(img); } else box.append(initials(c.name));
    return h("button", { class: "poster", "data-id": c.id, onclick: () => openVod(c) }, box, c.resume && c.resume.duration ? h("div", { class: "res" }, h("i", { style: `width:${(c.resume.position / c.resume.duration) * 100}%` })) : null, h("div", { class: "n" }, cleanTitle(c.name)));
  };
  const backfill = async (container) => { const ids = $$(".poster", container).filter((p) => !$("img", p)).map((p) => p.dataset.id).filter(Boolean).slice(0, 24); if (!ids.length) return; try { const r = await post("/vod/posters", { ids }); for (const [id, url] of Object.entries(r.posters)) { const p = $(`.poster[data-id="${id}"]`, container); if (p) { const box = $(".img", p); const img = h("img", { src: url, alt: "" }); fitLogo(img, box); box.replaceChildren(img); } } } catch (_) { /* */ } };

  const renderBrowse = async () => {
    const b = state.browse;
    const v = h("div");
    const sections = { live: "Direct", sports: "Sports", movies: "Films", series: "Séries", favorites: "Favoris", apps: "Apps", search: "Recherche" };
    v.append(h("div", { class: "chips" }, Object.entries(sections).map(([k, l]) => h("button", { class: "chip" + (b.section === k ? " on" : ""), onclick: () => { b.section = k; b.group = null; renderBrowse(); } }, l))));
    const body = h("div");
    const loading = ["movies", "series"].includes(b.section) && !(b.group || b.q || b.all) ? skel.posters() : skel.lines(6);
    v.append(body, loading);
    main.replaceChildren(v);
    try {
      if (b.section === "live") {
        const input = h("input", { class: "input", placeholder: "Filtrer…", value: b.q || "" });
        let timer; input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { b.q = input.value.trim(); renderBrowse(); }, 400); });
        body.append(h("div", { class: "search" }, input));
        const d = await api(`/channels?kind=live&limit=200` + (b.group ? "&group=" + encodeURIComponent(b.group) : "") + (b.q ? "&q=" + encodeURIComponent(b.q) : ""));
        if (d.groups.length > 1) body.append(h("select", { class: "input", onchange: (e) => { b.group = e.target.value || null; renderBrowse(); } }, h("option", { value: "" }, "Tous les groupes"), d.groups.map((g) => h("option", { value: g.name, selected: g.name === b.group }, `${groupLabel(g.name)} (${g.count})`))));
        if (!d.items.length) body.append(emptyState("tv", "Aucune chaîne ici", "Ajoute une source IPTV dans Réglages : compte Xtream, lien M3U ou chaînes gratuites.", h("button", { class: "btn primary block", onclick: () => showTab("settings") }, I("plus"), "Ajouter une source")));
        if (d.items.length) body.append(h("div", { class: "card list-card" }, d.items.map(chanItem)));
      } else if (b.section === "movies" || b.section === "series") {
        const kind = b.section === "movies" ? "vod" : "series";
        const label = kind === "vod" ? "films" : "séries";
        if (b.group || b.q || b.all) {
          const input = h("input", { class: "input", placeholder: "Filtrer…", value: b.q || "" });
          let timer; input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { b.q = input.value.trim(); b.offset = 0; renderBrowse(); }, 400); });
          body.append(h("div", { class: "search" }, input, h("button", { class: "btn", onclick: () => { b.group = null; b.q = ""; b.all = false; b.offset = 0; renderBrowse(); } }, I("close"))));
          const d = await api(`/vod/browse?kind=${kind}&limit=60&offset=${b.offset || 0}&sort=${b.sort || "added"}` + (b.group ? "&group=" + encodeURIComponent(b.group) : "") + (b.q ? "&q=" + encodeURIComponent(b.q) : ""));
          body.append(h("select", { class: "input", onchange: (e) => { b.group = e.target.value || null; b.offset = 0; renderBrowse(); } }, h("option", { value: "" }, `Tous (${d.total})`), d.groups.map((g) => h("option", { value: g.name, selected: g.name === b.group }, `${groupLabel(g.name)} (${g.count})`))));
          body.append(h("div", { class: "chips" }, [["added", "Récents"], ["rating", "Notés"], ["name", "A à Z"]].map(([k, l]) => h("button", { class: "chip" + ((b.sort || "added") === k ? " on" : ""), onclick: () => { b.sort = k; b.offset = 0; renderBrowse(); } }, l))));
          if (!d.items.length) body.append(emptyState("search", "Aucun titre", "Aucun titre ne correspond à ce filtre."));
          body.append(h("div", { class: "posters" }, d.items.map(posterItem)));
          const off = b.offset || 0;
          body.append(h("div", { class: "row", style: "margin-top:12px" }, off > 0 ? h("button", { class: "btn grow", onclick: () => { b.offset = Math.max(0, off - 60); renderBrowse(); } }, I("arrow-left"), "Précédent") : null, off + d.items.length < d.total ? h("button", { class: "btn grow", onclick: () => { b.offset = off + 60; renderBrowse(); } }, "Suivant", I("arrow-right")) : null));
          backfill(body);
        } else {
          const d = await api("/vod/home?kind=" + kind);
          if (!d.total) {
            body.append(emptyState(kind === "vod" ? "film" : "stack", kind === "vod" ? "Aucun film pour l'instant" : "Aucune série pour l'instant", kind === "vod" ? "Les films arrivent avec ta source IPTV : un compte Xtream ou une playlist M3U avec vidéo à la demande. Sans abonnement, les classiques libres de droits se lisent tout de suite." : "Les séries arrivent avec un compte Xtream ou une playlist M3U qui contient une section séries.",
              h("button", { class: "btn primary block", onclick: () => { showTab("settings"); } }, I("plus"), "Ajouter une source"),
              kind === "vod" ? h("button", { class: "btn block", onclick: (e) => busy(e.currentTarget, async () => { const s = await post("/sources", { type: "free", bundle: "archive-films" }); toast(`${s.item_count} films ajoutés`); renderBrowse(); }) }, I("plus"), "Films classiques gratuits") : null));
            return;
          }
          if (d.hero) { const ex = d.hero.extra || {}; body.append(h("button", { class: "hero", onclick: () => openVod(d.hero) }, h("div", { class: "bg", style: d.hero.logo ? `background-image:url("${d.hero.logo}")` : "" }), d.hero.logo ? h("div", { class: "art" }, h("img", { src: d.hero.logo, alt: "" })) : null, h("div", { class: "txt" }, h("div", { class: "k" }, "À la une"), h("div", { class: "t" }, cleanTitle(d.hero.name)), h("div", { class: "d" }, ex.year ? h("span", { class: "badge pill num" }, String(ex.year)) : null, ex.rating && Number(ex.rating) > 0 ? h("span", { class: "badge pill" }, rating(ex.rating)) : null, d.hero.group ? h("span", { class: "badge pill" }, groupLabel(d.hero.group)) : null)))); }
          body.append(h("div", { class: "chips" }, h("button", { class: "chip on", onclick: () => { b.all = true; renderBrowse(); } }, `Tout (${d.total} ${label})`), d.groups.slice(0, 10).map((g) => h("button", { class: "chip", onclick: () => { b.group = g.name; b.offset = 0; renderBrowse(); } }, groupLabel(g.name)))));
          d.rows.forEach((row) => { body.append(h("h2", {}, groupLabel(row.title)), h("div", { class: "hrow" }, row.items.map(posterItem))); });
          backfill(body);
        }
      } else if (b.section === "sports") {
        const sports = { all: "Tous", football: "Foot", f1: "F1", ufc: "UFC", basketball: "Basket", boxing: "Boxe", other: "Autres" };
        body.append(h("div", { class: "chips" }, Object.entries(sports).map(([k, l]) => h("button", { class: "chip" + (b.sport === k ? " on" : ""), onclick: () => { b.sport = k; renderBrowse(); } }, l))));
        const d = await api("/sports" + (b.sport !== "all" ? "?sport=" + b.sport : ""));
        if (!d.events.length) body.append(emptyState("calendar", "Aucun événement à venir", "Le calendrier se met à jour tout seul dès que le boîtier est en ligne."));
        let day = ""; const card = h("div", { class: "card list-card" });
        d.events.forEach((ev) => { const dd = ev.start ? fmtDate(ev.start) : "À confirmer"; if (dd !== day) { day = dd; card.append(h("div", { class: "day" }, dd)); } card.append(h("button", { class: "event", onclick: () => openEvent(ev) }, h("div", { class: "w" }, ev.start ? fmtTime(ev.start) : "—", h("small", {}, I(sportIcon(ev.sport)), sportLabel(ev.sport))), h("div", {}, h("div", { class: "n" }, ev.name), h("div", { class: "l" }, [ev.session, ev.location].filter(Boolean).join(" · "))), h("span", { class: "badge " + (ev.status === "live" ? "live" : ev.status === "past" ? "" : "soon") }, ev.status === "live" ? "Direct" : ev.status === "past" ? "Fini" : "Prévu"))); });
        if (d.events.length) body.append(card);
      } else if (b.section === "favorites") {
        const d = await api("/favorites");
        body.append(d.items.length ? h("div", { class: "card list-card" }, d.items.map(chanItem)) : emptyState("star", "Pas encore de favoris", "Touche l'étoile à côté d'une chaîne pour la retrouver ici."));
        const ml = await api("/mylist");
        if (ml.items.length) { body.append(h("h2", {}, "Ma liste (liens perso)")); body.append(h("div", { class: "card list-card" }, ml.items.map((m) => h("button", { class: "item", onclick: () => playOnTv({ id: m.id, name: m.title, kind: m.kind }) }, m.poster ? h("img", { class: "logo", src: m.poster, alt: "" }) : h("div", { class: "ph" }, initials(m.title)), h("div", { class: "grow" }, h("div", { class: "n" }, m.title), h("div", { class: "s" }, m.year || m.kind)), h("button", { class: "act", onclick: async (e) => { e.stopPropagation(); await api("/mylist/" + m.id, { method: "DELETE" }); renderBrowse(); } }, I("trash")))))); }
        body.append(h("button", { class: "btn block", style: "margin-top:12px", onclick: addLinkSheet }, I("link"), "Ajouter un lien vidéo perso"));
      } else if (b.section === "apps") {
        const d = await api("/apps");
        if (!d.kiosk) body.append(emptyState("monitor", "Navigateur du boîtier injoignable", "Les apps s'ouvrent uniquement sur le vrai boîtier, pas en mode développement."));
        body.append(h("div", { class: "apps" }, d.apps.map((a) => h("button", { class: "app", style: `background:${a.color}`, onclick: async () => { try { await post("/apps/" + a.key); toast(a.name + " ouvert sur la TV · passe en Trackpad"); showTab("remote"); } catch (e) { toast(e.message, true); } } }, a.name))));
        body.append(h("div", { class: "muted small", style: "margin-top:12px" }, "Netflix, Prime, Canal+… s'ouvrent dans le navigateur du boîtier (Widevine, 720p/1080p max sous Linux). Connecte-toi une fois avec le trackpad et le clavier de la télécommande ; la session reste mémorisée."));
      } else if (b.section === "search") {
        const input = h("input", { class: "input", placeholder: "Chaîne, film, série, événement…", value: b.q || "", autofocus: true });
        const res = h("div");
        let timer;
        const run = async () => { const q = input.value.trim(); b.q = q; res.replaceChildren(); if (q.length < 2) return; const d = await api("/search?q=" + encodeURIComponent(q)); if (d.live.length) res.append(h("h2", {}, "Chaînes"), h("div", { class: "card list-card" }, d.live.map(chanItem))); if (d.sports.length) res.append(h("h2", {}, "Événements"), h("div", { class: "card list-card" }, d.sports.map((ev) => h("button", { class: "event", onclick: () => openEvent(ev) }, h("div", { class: "w" }, ev.start ? fmtTime(ev.start) : "—", h("small", {}, ev.start ? fmtDate(ev.start) : "")), h("div", {}, h("div", { class: "n" }, ev.name), h("div", { class: "l" }, ev.session)), h("span", {}))))); if (d.vod.length) res.append(h("h2", {}, "Films"), h("div", { class: "posters" }, d.vod.map(posterItem))); if (d.series.length) res.append(h("h2", {}, "Séries"), h("div", { class: "posters" }, d.series.map(posterItem))); if (!res.children.length) res.append(emptyState("search", "Rien trouvé", "Essaie un autre mot : chaîne, film, équipe ou compétition.")); };
        input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(run, 350); });
        body.append(h("div", { class: "search" }, input), res);
        if (b.q) run();
      }
    } catch (e) { body.append(emptyState("info", "Chargement impossible", e.message, h("button", { class: "btn block", onclick: renderBrowse }, I("refresh"), "Réessayer"))); }
    finally { loading.remove(); }
  };

  const openEvent = (ev) => sheet(async (b) => {
    b.append(h("h1", {}, ev.name), h("p", {}, [ev.session, ev.location, ev.start ? fmtDate(ev.start) + " " + fmtTime(ev.start) : ""].filter(Boolean).join(" · ")));
    const list = h("div", { class: "card list-card" }, skel.lines(3));
    b.append(list);
    try { const d = await api("/sports/" + ev.id + "/streams"); list.replaceChildren(); if (!d.streams.length) list.replaceWith(emptyState("tv", "Aucune chaîne trouvée", "Aucune chaîne de tes sources ne semble diffuser cet événement.")); d.streams.forEach((c) => list.append(chanItem(c))); } catch (e) { list.replaceChildren(h("div", { class: "empty" }, e.message)); }
  });
  const fmtMin = (m) => (m ? (m >= 60 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")}` : `${m} min`) : "");
  const openVod = (c) => sheet(async (b) => {
    const title = h("h1", {}, cleanTitle(c.name));
    const img = h("img", { src: c.logo || "", alt: "" });
    const meta = h("div", { class: "meta" });
    const actions = h("div", { class: "row", style: "gap:8px;margin:10px 0;flex-wrap:wrap" });
    const p = h("p", {}, (c.extra && c.extra.plot) || c.group || "");
    const cast = h("p", { class: "small" });
    const extra = h("div");
    if (c.kind !== "series") actions.append(h("button", { class: "btn primary grow", onclick: () => { playOnTv(c); closeSheet(); } }, I("play"), "Lire sur la TV"));
    actions.append(favBtn(c));
    b.append(title, h("div", { class: "cover" }, img, h("div", { class: "grow" }, meta, actions)), p, cast, extra);
    try {
      const d = await api("/vod/" + c.id); const m = d.meta;
      title.textContent = m.title || cleanTitle(c.name);
      if (m.poster) img.src = m.poster;
      meta.replaceChildren(...[m.year ? String(m.year) : "", m.runtime ? fmtMin(m.runtime) : "", m.rating && Number(m.rating) > 0 ? rating(m.rating) : "", ...(m.genres || []).slice(0, 3)].filter(Boolean).map((x) => h("span", { class: "badge pill" }, x)));
      if (m.overview) p.textContent = m.overview;
      if (m.cast && m.cast.length) cast.textContent = (m.director ? "Réalisé par " + m.director + " · " : "") + "Avec " + m.cast.slice(0, 5).join(", ");
      if (d.resume && d.resume.duration && d.resume.position > 30) actions.prepend(h("button", { class: "btn primary grow", onclick: () => { playOnTv(c, { position: d.resume.position }); closeSheet(); } }, I("play"), `Reprendre ${fmtDur(d.resume.position)}`));
      if (m.trailer) actions.append(h("button", { class: "btn", onclick: () => post("/apps/trailer", { url: m.trailer }).then(() => toast("Bande-annonce sur la TV")).catch((e) => toast(e.message, true)) }, I("clapper")));
      if (c.kind === "series") {
        const eps = d.episodes || [];
        if (!eps.length) extra.append(emptyState("stack", "Aucun épisode", "Le fournisseur n'a pas encore publié d'épisode pour cette série."));
        else {
          const seasons = [...new Set(eps.map((e) => e.season))];
          const list = h("div", { class: "card list-card" });
          const chips = h("div", { class: "chips" });
          const show = (s) => { $$(".chip", chips).forEach((x) => x.classList.toggle("on", String(x.dataset.s) === String(s))); list.replaceChildren(); eps.filter((e) => e.season === s).forEach((e, i) => { const name = `${cleanTitle(c.name)} · S${String(s).padStart(2, "0")}E${String(e.episode || i + 1).padStart(2, "0")}`; list.append(h("button", { class: "item", onclick: () => { playOnTv({ name, url: e.url, kind: "vod" }); closeSheet(); } }, e.image ? h("img", { class: "logo", src: e.image, alt: "" }) : h("div", { class: "ph" }, String(e.episode || i + 1)), h("div", { class: "grow" }, h("div", { class: "n" }, `${i + 1}. ${e.title}`), h("div", { class: "s" }, e.plot || e.duration || "")))); }); };
          seasons.forEach((s) => chips.append(h("button", { class: "chip", "data-s": s, onclick: () => show(s) }, "Saison " + s)));
          extra.append(chips, list); show(seasons[0]);
        }
      }
      if (d.similar && d.similar.length) { extra.append(h("h2", {}, "Dans le même genre"), h("div", { class: "hrow" }, d.similar.map(posterItem))); backfill(extra); }
    } catch (e) { toast(e.message, true); }
  });
  const openSeries = openVod;
  const addLinkSheet = () => sheet((b) => {
    const title = h("input", { class: "input", placeholder: "Nom affiché" }), url = h("input", { class: "input", placeholder: "https://…", autocapitalize: "off", inputmode: "url" }), kind = h("select", { class: "input" }, h("option", { value: "vod" }, "Vidéo, film ou replay"), h("option", { value: "live" }, "Direct"));
    b.append(h("h1", {}, "Ajouter un lien"), field("Titre", title), field("Adresse du flux", url, "http, https, rtmp ou rtsp"), field("Type", kind),
      h("button", { class: "btn primary block", onclick: (e) => busy(e.currentTarget, async () => { await post("/mylist", { title: title.value, url: url.value, kind: kind.value }); closeSheet(); renderBrowse(); toast("Ajouté à Ma liste"); }) }, I("plus"), "Ajouter"), h("div", { class: "form-error" }));
  });

  // ---------------------------------------------------------------- settings
  const renderSettings = async () => {
    const v = h("div", {}, h("h1", {}, "Réglages"));
    main.replaceChildren(v);
    let setup;
    try { setup = await api("/setup"); } catch (e) { v.append(h("div", { class: "empty" }, e.message)); return; }
    if (!setup.setup_done && !setup.configured) { v.append(wizard(setup)); return; }
    v.append(h("div", { class: "card" }, h("div", { class: "row" }, h("div", { class: "grow" }, h("div", {}, h("b", {}, "Bibliothèque")), h("div", { class: "muted small" }, `${setup.counts.live} chaînes · ${setup.counts.vod} films · ${setup.counts.series} séries`)), h("button", { class: "btn", onclick: (e) => busy(e.currentTarget, async () => { await post("/refresh"); toast("Tout est à jour"); renderSettings(); }) }, I("refresh")))));
    const items = [
      ["signal", "Sources IPTV", "Xtream, M3U, chaînes gratuites", sourcesSheet],
      ["guide", "Guide TV", "Programmes en cours et à venir", epgSheet],
      ["wifi", "Réseau", "Wi-Fi, Ethernet, hotspot", networkSheet],
      ["play-circle", "Lecture", "Fluidité du direct, test des sources", playbackSheet],
      ["film", "Métadonnées", "Affiches et résumés des films", tmdbSheet],
      ["compass", "Assistant de configuration", "Refaire les étapes", () => { v.replaceChildren(h("h1", {}, "Configuration"), wizard(setup)); }],
      ["monitor", "Système", "Audio, mise à jour, redémarrage, journaux", systemSheet],
    ];
    v.append(h("div", { class: "card list-card" }, items.map(([icon, t, sub, fn]) => h("button", { class: "item", onclick: fn }, h("span", { class: "set-icon" }, I(icon)), h("div", { class: "grow" }, h("div", { class: "n" }, t), h("div", { class: "s" }, sub)), h("span", { class: "chev" }, I("chevron-right"))))));
    v.append(h("div", { class: "muted small center", style: "margin-top:20px" }, "Aura · interface de configuration"));
  };

  const wizard = (setup) => {
    const w = h("div");
    let step = setup.network_online ? 1 : 0;
    const steps = ["Réseau", "Source IPTV", "Guide TV", "Terminé"];
    const draw = async () => {
      w.replaceChildren(h("div", { class: "steps" }, steps.map((_, i) => h("span", { class: i <= step ? "on" : "" }))), h("h2", {}, `Étape ${step + 1} / ${steps.length} · ${steps[step]}`));
      const box = h("div", { class: "card" });
      w.append(box);
      if (step === 0) {
        const net = await api("/network");
        box.append(h("p", { class: "muted" }, net.online ? `Le boîtier est en ligne (${net.addresses.join(", ")}).` : "Le boîtier n'a pas d'accès Internet. Branche un câble Ethernet, ou choisis un Wi-Fi ci-dessous."));
        box.append(wifiPicker(() => draw()));
        box.append(h("button", { class: "btn primary block", style: "margin-top:12px", onclick: () => { step = 1; draw(); } }, net.online ? "Continuer" : "Continuer quand même"));
      } else if (step === 1) {
        box.append(sourceForm(() => { step = 2; draw(); }));
        box.append(h("button", { class: "btn ghost block", onclick: () => { step = 2; draw(); } }, "Passer"));
      } else if (step === 2) {
        const d = await api("/epg/sources");
        box.append(h("p", { class: "muted" }, "Le guide TV affiche « en ce moment / ensuite » sur les chaînes et sert à trouver les chaînes qui diffusent un événement. Les sources Xtream/M3U ajoutent souvent leur propre EPG automatiquement."));
        d.free.forEach((f) => box.append(h("button", { class: "btn block", style: "margin-bottom:8px", onclick: (e) => busy(e.currentTarget, async () => { await post("/epg/sources", { key: f.key }); toast("EPG ajouté, chargement en arrière-plan"); }) }, I("plus"), f.name)));
        box.append(h("button", { class: "btn primary block", style: "margin-top:8px", onclick: () => { step = 3; draw(); } }, "Continuer"));
      } else {
        box.append(h("p", {}, "C'est prêt. La TV affiche maintenant l'accueil Aura. Tu peux ajouter une clé TMDB (gratuite) pour les affiches de films dans Réglages › Métadonnées."));
        box.append(h("button", { class: "btn primary block", onclick: async () => { await api("/settings", { method: "PUT", body: { values: { setup_done: "1" } } }); renderSettings(); } }, "Terminer"));
      }
    };
    draw();
    return w;
  };

  const wifiPicker = (onDone) => {
    const box = h("div");
    const list = h("div", { class: "card list-card", style: "padding:6px 10px" }, skel.lines(3));
    box.append(list);
    api("/network/wifi").then((d) => {
      list.replaceChildren();
      if (!d.networks.length) list.append(h("div", { class: "empty" }, "Aucun réseau détecté."));
      d.networks.forEach((n) => list.append(h("button", { class: "item", onclick: () => sheet((b) => {
        const pw = h("input", { class: "input", type: "password", placeholder: "Mot de passe" });
        b.append(h("h1", {}, n.ssid), n.security ? field("Mot de passe du réseau", pw) : h("p", {}, "Réseau ouvert, aucun mot de passe."), h("button", { class: "btn primary block", onclick: (e) => busy(e.currentTarget, async () => { const r = await post("/network/wifi", { ssid: n.ssid, password: pw.value }); toast(r.message || "Connecté"); closeSheet(); onDone(); }) }, I("wifi"), "Se connecter"), h("div", { class: "form-error" }));
      }) }, h("div", { class: "grow" }, h("div", { class: "n" }, n.ssid), h("div", { class: "s" }, `${n.security || "ouvert"} · ${n.signal}%${n.active ? " · connecté" : ""}`)), h("span", { class: "chev" }, I("chevron-right")))));
    }).catch((e) => list.replaceChildren(h("div", { class: "empty" }, e.message)));
    return box;
  };

  const sourceForm = (onAdded) => {
    const box = h("div");
    const seg = h("div", { class: "seg" });
    const area = h("div");
    const forms = {
      xtream: () => {
        const url = h("input", { class: "input", placeholder: "http://exemple.tv:8080", autocapitalize: "off", inputmode: "url" }), u = h("input", { class: "input", autocapitalize: "off", autocomplete: "username" }), p = h("input", { class: "input", type: "password", autocomplete: "current-password" });
        return h("div", {}, h("p", { class: "muted small" }, "Ton fournisseur t'a donné une adresse, un identifiant et un mot de passe. Direct, films, séries et guide arrivent d'un coup."),
          field("Adresse du serveur", url, "Avec le port, par exemple :8080"), field("Identifiant", u), field("Mot de passe", p),
          h("button", { class: "btn primary block", onclick: (e) => busy(e.currentTarget, async () => { const s = await post("/sources", { type: "xtream", url: url.value, username: u.value, password: p.value }); toast(`${s.name} : ${s.item_count} éléments`); onAdded(); }) }, I("link"), "Connecter"), h("div", { class: "form-error" }));
      },
      m3u: () => {
        const url = h("input", { class: "input", placeholder: "https://…/playlist.m3u", autocapitalize: "off", inputmode: "url" }), epg = h("input", { class: "input", placeholder: "https://…/guide.xml", autocapitalize: "off", inputmode: "url" }), file = h("input", { class: "input", type: "file", accept: ".m3u,.m3u8,.txt" });
        return h("div", {}, field("Lien de la playlist M3U", url), field("Lien du guide TV", epg, "Facultatif, format XMLTV"),
          h("div", {}, h("button", { class: "btn primary block", onclick: (e) => busy(e.currentTarget, async () => { const s = await post("/sources", { type: "m3u_url", url: url.value, epg_url: epg.value }); toast(`${s.name} : ${s.item_count} éléments`); onAdded(); }) }, I("plus"), "Ajouter la playlist"), h("div", { class: "form-error" })),
          h("div", { style: "margin-top:16px" }, field("Ou un fichier", file), h("button", { class: "btn block", onclick: (e) => busy(e.currentTarget, async () => { if (!file.files[0]) throw new Error("Choisis d'abord un fichier."); const fd = new FormData(); fd.append("file", file.files[0]); const s = await api("/sources/upload", { method: "POST", body: fd }); toast(`${s.name} : ${s.item_count} éléments`); onAdded(); }) }, I("update"), "Importer le fichier"), h("div", { class: "form-error" })));
      },
      free: () => { const box2 = h("div", {}, h("p", { class: "muted small" }, "Sans abonnement : chaînes publiques de la communauté iptv-org et films classiques libres de droits. Qualité variable, aucun compte.")); api("/sources").then((d) => d.bundles.forEach((bd) => box2.append(h("button", { class: "btn block", style: "margin-bottom:8px;justify-content:flex-start", onclick: (e) => busy(e.currentTarget, async () => { const s = await post("/sources", { type: "free", bundle: bd.key }); toast(`${s.name} : ${s.item_count} chaînes`); onAdded(); }) }, I("plus"), bd.name)))); return box2; },
    };
    const set = (k) => { $$("button", seg).forEach((b) => b.classList.toggle("on", b.dataset.k === k)); area.replaceChildren(forms[k]()); };
    [["xtream", "Xtream"], ["m3u", "M3U"], ["free", "Gratuit"]].forEach(([k, l]) => seg.append(h("button", { "data-k": k, onclick: () => set(k) }, l)));
    box.append(seg, area);
    set("xtream");
    return box;
  };

  const sourcesSheet = () => sheet(async (b) => {
    b.append(h("h1", {}, "Sources IPTV"));
    const list = h("div", { class: "card list-card" });
    const draw = async () => { const d = await api("/sources"); list.replaceChildren(); if (!d.sources.length) list.append(h("div", { class: "empty" }, "Aucune source.")); d.sources.forEach((s) => list.append(h("div", { class: "item" }, h("div", { class: "grow" }, h("div", { class: "n" }, s.name), h("div", { class: "s " + (s.status.startsWith("erreur") ? "status-err" : "") }, `${s.type} · ${s.item_count} éléments · ${s.status || "—"} · ${ago(s.last_refresh)}`)), h("button", { class: "act", onclick: (e) => busy(e.currentTarget, async () => { await post(`/sources/${s.id}/refresh`); draw(); }) }, I("refresh")), h("button", { class: "act" + (s.enabled ? " on" : ""), onclick: async () => { await api(`/sources/${s.id}`, { method: "PATCH", body: { enabled: !s.enabled } }); draw(); } }, I(s.enabled ? "check" : "close")), h("button", { class: "act", onclick: async () => { if (confirm(`Supprimer « ${s.name} » ?`)) { await api(`/sources/${s.id}`, { method: "DELETE" }); draw(); } } }, I("trash"))))); };
    b.append(list, h("h2", {}, "Ajouter"), sourceForm(draw));
    draw();
  });
  const epgSheet = () => sheet(async (b) => {
    b.append(h("h1", {}, "Guide TV (EPG)"));
    const list = h("div", { class: "card list-card" });
    const draw = async () => { const d = await api("/epg/sources"); list.replaceChildren(h("div", { class: "muted small", style: "padding:6px" }, "Dernier chargement : " + ago(d.last_refresh))); d.sources.forEach((s) => list.append(h("div", { class: "item" }, h("div", { class: "grow" }, h("div", { class: "n" }, s.name), h("div", { class: "s" }, s.status || "—")), h("button", { class: "act", onclick: async () => { await api(`/epg/sources/${s.id}`, { method: "DELETE" }); draw(); } }, I("trash"))))); const url = h("input", { class: "input", placeholder: "URL XMLTV personnalisée (.xml ou .xml.gz)", autocapitalize: "off" }); list.append(h("div", { style: "padding:8px 0" }, d.free.map((f) => h("button", { class: "btn block", style: "margin-bottom:8px", onclick: (e) => busy(e.currentTarget, async () => { await post("/epg/sources", { key: f.key }); draw(); }) }, I("plus"), f.name)), url, h("button", { class: "btn block", onclick: (e) => busy(e.currentTarget, async () => { await post("/epg/sources", { url: url.value }); draw(); }) }, I("plus"), "Ajouter l'adresse"), h("button", { class: "btn primary block", style: "margin-top:8px", onclick: (e) => busy(e.currentTarget, async () => { await post("/epg/refresh"); toast("EPG rechargé"); draw(); }) }, I("refresh"), "Recharger le guide maintenant"))); };
    b.append(list); draw();
  });
  const networkSheet = () => sheet(async (b) => {
    b.append(h("h1", {}, "Réseau"));
    const net = await api("/network");
    b.append(h("div", { class: "card" }, h("div", { class: "kv" }, h("div", { class: "k" }, "État"), h("div", { class: net.online ? "status-ok" : "status-err" }, net.online ? "En ligne" : "Hors ligne"), h("div", { class: "k" }, "Wi-Fi"), h("div", {}, net.wifi_ssid || "—"), h("div", { class: "k" }, "Adresses"), h("div", {}, net.addresses.join(", ") || "—"), h("div", { class: "k" }, "Accès"), h("div", {}, net.urls.map((u) => h("div", {}, u))), h("div", { class: "k" }, "Hotspot"), h("div", {}, net.wifi_ap_capable ? (net.hotspot_active ? "actif" : "possible") : "non supporté par la carte Wi-Fi"))));
    b.append(h("h2", {}, "Wi-Fi"), wifiPicker(() => { closeSheet(); toast("Réseau mis à jour"); }));
    if (net.wifi_ssid && !net.hotspot_active) b.append(h("button", { class: "btn block", onclick: async () => { await api("/network/wifi/" + encodeURIComponent(net.wifi_ssid), { method: "DELETE" }); toast("Réseau oublié"); closeSheet(); } }, "Oublier « " + net.wifi_ssid + " »"));
    b.append(h("h2", {}, "Ethernet"));
    const ip = h("input", { class: "input", placeholder: "IP fixe, ex. 192.168.1.50/24" }), gw = h("input", { class: "input", placeholder: "Passerelle, ex. 192.168.1.1" }), dns = h("input", { class: "input", placeholder: "DNS, ex. 1.1.1.1 9.9.9.9", value: "1.1.1.1 9.9.9.9" });
    b.append(field("Adresse IP fixe", ip, "Exemple : 192.168.1.50/24"), field("Passerelle", gw), field("DNS", dns), h("div", { class: "row" }, h("button", { class: "btn grow", onclick: (e) => busy(e.currentTarget, async () => { await post("/network/ethernet/static", { ip_cidr: ip.value, gateway: gw.value, dns: dns.value }); toast("IP fixe appliquée"); }) }, "IP fixe"), h("button", { class: "btn grow", onclick: (e) => busy(e.currentTarget, async () => { await post("/network/ethernet/dhcp"); toast("DHCP appliqué"); }) }, "DHCP")));
    if (net.wifi_ap_capable) b.append(h("h2", {}, "Hotspot de configuration"), h("button", { class: "btn block", onclick: (e) => busy(e.currentTarget, async () => { const r = net.hotspot_active ? await api("/network/hotspot", { method: "DELETE" }) : await post("/network/hotspot"); toast(r.message || "OK"); closeSheet(); }) }, net.hotspot_active ? "Arrêter le hotspot" : `Démarrer le hotspot « ${net.hotspot_ssid} »`));
  });
  const playbackSheet = () => sheet(async (b) => {
    const s = (await api("/settings")).settings;
    const opt = (pairs, current) => pairs.map(([v, l]) => h("option", { value: v, selected: current === v }, l));
    const engine = h("select", { class: "input" }, opt([["auto", "Automatique (recommandé)"], ["browser", "Navigateur"], ["mpv", "mpv, décodage par la carte graphique"]], s.live_backend || "auto"));
    const fast = h("select", { class: "input" }, opt([["1", "Activé : teste les sources et garde la plus rapide"], ["0", "Désactivé"]], s.fast_start || "1"));
    b.append(
      h("h1", {}, "Lecture"),
      h("label", { class: "lbl" }, "Moteur vidéo pour le direct"), engine,
      h("p", { class: "small" }, "Sur un Mac mini ou un vieux PC, mpv décode avec la carte graphique : les chaînes en .ts ne saccadent plus."),
      h("label", { class: "lbl" }, "Démarrage rapide"), fast,
      h("button", { class: "btn primary block", onclick: (e) => busy(e.currentTarget, async () => { await api("/settings", { method: "PUT", body: { values: { live_backend: engine.value, fast_start: fast.value } } }); toast("Réglages de lecture enregistrés"); closeSheet(); }) }, "Enregistrer"),
    );
    const q = h("input", { class: "input", placeholder: "Chaîne à tester, ex. France 24" });
    const out = h("div", { class: "card list-card" }, h("div", { class: "muted small" }, "Mesure chaque source de la chaîne : en ligne ou non, temps de réponse, lecture directe ou via le boîtier."));
    const run = (e) => busy(e.currentTarget, async () => {
      const r = await api("/search?q=" + encodeURIComponent(q.value.trim()));
      const ch = r.live[0];
      if (!ch) throw new Error("aucune chaîne trouvée");
      const d = await api("/player/probe?channel_id=" + ch.id);
      out.replaceChildren(h("div", { class: "muted small", style: "padding:6px" }, d.channel), ...d.sources.map((x) => h("div", { class: "item" }, h("div", { class: "grow" }, h("div", { class: "n" }, x.name), h("div", { class: "s " + (x.ok ? "status-ok" : "status-err") }, x.ok ? `${x.latency_ms} ms · ${x.cors ? "lecture directe" : "via le boîtier"}` : (x.error || "hors ligne"))))));
    });
    b.append(h("h2", {}, "Tester une chaîne"), field("Chaîne", q), h("div", {}, h("button", { class: "btn block", onclick: run }, I("signal"), "Tester"), h("div", { class: "form-error" })), out);
  });
  const tmdbSheet = () => sheet(async (b) => {
    const s = (await api("/settings")).settings;
    const keyInput = h("input", { class: "input", placeholder: s.tmdb_api_key_set ? "Clé enregistrée (laisser vide pour garder)" : "Clé API TMDB (v3)", autocapitalize: "off" });
    b.append(h("h1", {}, "Métadonnées TMDB"), h("p", {}, "Gratuit : crée un compte sur ", h("a", { href: "https://www.themoviedb.org/settings/api", target: "_blank" }, "themoviedb.org"), ", copie la clé API (v3) ici. Elle sert aux affiches, résumés et notes des films et séries."), field("Clé API TMDB, version 3", keyInput),
      h("button", { class: "btn primary block", onclick: (e) => busy(e.currentTarget, async () => { await api("/settings", { method: "PUT", body: { values: { tmdb_api_key: keyInput.value.trim() } } }); toast("Clé enregistrée"); closeSheet(); }) }, I("check"), "Enregistrer"), h("div", { class: "form-error" }));
  });
  const systemSheet = () => sheet(async (b) => {
    b.append(h("h1", {}, "Système"));
    const s = await api("/system");
    b.append(h("div", { class: "card" }, h("div", { class: "kv" }, h("div", { class: "k" }, "Version"), h("div", {}, "Aura " + s.version), h("div", { class: "k" }, "Machine"), h("div", {}, s.model || s.platform), h("div", { class: "k" }, "Disque libre"), h("div", {}, Math.round(s.disk_free / 1e9) + " Go"), h("div", { class: "k" }, "Mémoire libre"), h("div", {}, s.mem_total ? Math.round(s.mem_free / 1e6) + " Mo" : "—"), h("div", { class: "k" }, "mpv / Chrome"), h("div", {}, `mpv ${s.mpv ? "présent" : "absent"} · Chrome ${s.chrome ? "présent" : "absent"}`))));
    if (s.audio && s.audio.length) { b.append(h("h2", {}, "Sortie audio")); const sel = h("select", { class: "input", onchange: async (e) => { await post("/system/audio/default?name=" + encodeURIComponent(e.target.value)); toast("Sortie audio changée"); } }, s.audio.map((a) => h("option", { value: a.name, selected: a.default === "yes" }, a.name))); b.append(sel); }
    const dn = h("input", { class: "input", placeholder: "Nom du boîtier (ex. Salon)" });
    b.append(h("h2", {}, "Nom"), field("Nom du boîtier", dn), h("button", { class: "btn block", onclick: (e) => busy(e.currentTarget, async () => { await api("/settings", { method: "PUT", body: { values: { device_name: dn.value } } }); toast("OK"); }) }, "Enregistrer"));
    b.append(h("h2", {}, "Actions"), h("div", { class: "keys", style: "grid-template-columns:repeat(2,1fr)" },
      h("button", { onclick: (e) => busy(e.currentTarget, async () => { const r = await post("/system/update"); toast(r.ok ? "Mise à jour lancée" : "Échec : " + r.output.slice(-200), !r.ok); }) }, I("update"), "Mettre à jour"),
      h("button", { onclick: (e) => busy(e.currentTarget, async () => { await post("/system/restart-kiosk"); toast("Affichage relancé"); }) }, I("monitor"), "Relancer l'affichage"),
      h("button", { onclick: () => confirm("Redémarrer le boîtier ?") && post("/system/reboot").then(() => toast("Redémarrage…")) }, I("restart"), "Redémarrer"),
      h("button", { onclick: () => confirm("Éteindre le boîtier ?") && post("/system/shutdown").then(() => toast("Extinction…")) }, I("power"), "Éteindre")));
    const logs = h("pre", { class: "logs" }, "…");
    b.append(h("h2", {}, "Journaux"), logs);
    api("/system/logs?lines=120").then((d) => (logs.textContent = d.logs || "(vide)")).catch((e) => (logs.textContent = e.message));
  });

  // ---------------------------------------------------------------- websocket
  const connectWs = () => {
    const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/api/ws?role=remote");
    state.ws = ws;
    ws.onopen = () => { $(".conn-text").textContent = "connecté"; $("#conn").className = "conn on"; };
    ws.onmessage = (ev) => { let m; try { m = JSON.parse(ev.data); } catch (_) { return; } if (m.type === "hello") { state.player = m.player || {}; } else if (m.type === "player") { state.player = m.state; } else if (m.type === "tv_view") { state.tvView = m.view; state.tvFocus = m.focus || ""; } else if (m.type === "catalog_changed" && state.tab === "browse") { renderBrowse(); return; } else return; if (state.tab === "remote") { const old = $("#now"); if (old) old.replaceWith(nowCard()); } };
    ws.onclose = () => { $(".conn-text").textContent = "hors ligne"; $("#conn").className = "conn off"; setTimeout(connectWs, 2000); };
    ws.onerror = () => ws.close();
  };
  setInterval(() => { if (state.ws && state.ws.readyState === 1) state.ws.send(JSON.stringify({ type: "ping" })); }, 25000);

  // ---------------------------------------------------------------- boot
  window.AuraIcons.hydrate();
  connectWs();
  api("/setup").then((s) => showTab(!s.configured && !s.setup_done ? "settings" : "remote")).catch(() => showTab("remote"));
})();
