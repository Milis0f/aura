/* Aura — TV UI. Vanilla JS, D-pad + mouse/keyboard navigation, hls.js / mpegts.js / native playback. */
(() => {
  "use strict";

  // ------------------------------------------------------------------ utils
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const h = (tag, attrs = {}, ...children) => {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") el.className = v;
      else if (k === "html") el.innerHTML = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "dataset") Object.assign(el.dataset, v);
      else if (v !== null && v !== undefined && v !== false) el.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      el.append(c.nodeType ? c : document.createTextNode(String(c)));
    }
    return el;
  };
  const api = async (path, opts = {}) => {
    const r = await fetch("/api" + path, { headers: { "Content-Type": "application/json" }, ...opts, body: opts.body && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body });
    if (!r.ok) { let msg = r.statusText; try { msg = (await r.json()).detail || msg; } catch (_) { /* */ } throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg)); }
    return r.json();
  };
  const post = (path, body) => api(path, { method: "POST", body });
  const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  const fmtDate = (ts) => new Date(ts * 1000).toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", month: "short" });
  const fmtDur = (s) => { s = Math.max(0, Math.floor(s || 0)); const hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60; return (hh ? hh + ":" : "") + String(mm).padStart(2, "0") + ":" + String(ss).padStart(2, "0"); };
  const fmtMin = (m) => (m ? (m >= 60 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")}` : `${m} min`) : "");
  const initials = (name) => (name || "?").replace(/[^A-Za-z0-9 ]/g, "").split(" ").filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";
  const progressPct = (p) => (p ? Math.min(100, Math.max(0, ((Date.now() / 1000 - p.start) / (p.stop - p.start)) * 100)) : 0);
  const cleanTitle = (n) => {
    let t = (n || "").trim().replace(/\.(mkv|mp4|avi|mov|m4v|webm|ts|wmv|flv)$/i, "");
    const release = (t.match(/\./g) || []).length >= 2 || t.includes("_");
    t = t.replace(/\b[\w-]+\.(com|net|org|tv|cc|me|io|to|xyz)\b/gi, " ");
    if (release) t = t.replace(/[._]/g, " ").replace(/\s*-\s*[A-Za-z0-9]+\s*$/, "");
    t = t.replace(/^\s*[A-Z]{2,3}\s*[-|:]\s*/, "");
    t = t.replace(/\b(FR|VF|VFF|VFQ|VO|VOST|VOSTFR|MULTI|TRUEFRENCH|FRENCH|2160p|1080p|1080i|720p|480p|4K|UHD|HD|SD|HDR|HDR10|BLURAY|BRRIP|BDRIP|WEBRIP|WEB-DL|WEBDL|HDRIP|DVDRIP|HDTV|x264|x265|H264|H265|HEVC|AVC|XVID|AAC|AC3|DTS|10BIT|REMUX|PROPER|REPACK)\b/gi, " ");
    return t.replace(/[\[\]{}|]+/g, " ").replace(/\(\s*\)/g, "").replace(/\s+/g, " ").replace(/\s*[-:]\s*$/, "").trim();
  };
  let toastTimer;
  const toast = (msg, isError = false) => { const t = $("#toast"); t.textContent = msg; t.classList.toggle("error", isError); t.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), 3500); };
  const I = (name, cls) => window.AuraIcons.svg(name, cls);
  const SPORTS = { f1: ["flag", "Formule 1"], ufc: ["octagon", "UFC"], boxing: ["glove", "Boxe"], football: ["ball", "Football"], basketball: ["basket", "Basket"], other: ["trophy", "Sport"] };
  const sportIcon = (sport) => (SPORTS[sport] || SPORTS.other)[0];
  const sportLabel = (sport) => (SPORTS[sport] || SPORTS.other)[1];
  const groupLabel = (g) => (!g || /^undefined$/i.test(g) ? "Autres" : g);
  const rating = (r) => h("span", { class: "rating" }, I("star-fill"), Number(r).toFixed(1));
  const stagger = (el, i) => { if (el && i < 14) { el.classList.add("rise"); el.style.setProperty("--i", i); } return el; };
  const emptyState = (icon, title, text, ...actions) => h("div", { class: "empty-state rise" }, h("div", { class: "es-icon" }, I(icon)), h("div", {}, h("div", { class: "es-title" }, title), h("div", { class: "es-text" }, text), actions.filter(Boolean).length ? h("div", { class: "es-actions" }, ...actions) : null));
  const fitLogo = (img, thumbEl) => { img.addEventListener("load", () => { if (img.naturalWidth && img.naturalWidth / img.naturalHeight > 0.9) thumbEl.classList.add("fit"); }); };
  const skeletons = {
    rows: () => h("div", { class: "view" }, h("div", { class: "sk sk-title" }), [0, 1].map(() => h("div", { class: "sk-row" }, Array.from({ length: 6 }, () => h("div", { class: "sk sk-card" }))))),
    posters: () => h("div", { class: "view" }, h("div", { class: "sk sk-banner" }), h("div", { class: "sk sk-title" }), h("div", { class: "sk-row" }, Array.from({ length: 8 }, () => h("div", { class: "sk sk-poster" })))),
    list: () => h("div", { class: "view" }, h("div", { class: "sk sk-title" }), h("div", { class: "sk-split" }, [8, 8].map((n) => h("div", {}, Array.from({ length: n }, () => h("div", { class: "sk sk-item" })))))),
    events: () => h("div", { class: "view" }, h("div", { class: "sk sk-title" }), Array.from({ length: 7 }, () => h("div", { class: "sk sk-event" }))),
  };
  const SKELETON_FOR = { home: "rows", library: "posters", live: "list", favorites: "list", sports: "events", f1: "events", ufc: "events", movies: "posters", series: "posters" };
  const skeletonItems = (n) => Array.from({ length: n }, () => h("div", { class: "sk sk-item" }));
  const setAmbient = (url) => { const a = $("#ambient"); if (url) { a.style.backgroundImage = `url("${url}")`; a.classList.add("on"); } else a.classList.remove("on"); };

  // ------------------------------------------------------------------ state
  const state = { view: "home", viewArg: null, zapList: [], zapIndex: -1, playing: null, alternatives: [], retries: 0, engine: null, ws: null, setupMode: false, last: {}, stack: [] };

  // ------------------------------------------------------------------ focus / spatial navigation
  let focused = null;
  const focusables = () => $$(".f").filter((el) => el.offsetParent !== null && !el.closest("[hidden]"));
  const setFocus = (el, scroll = true) => {
    if (!el) return;
    if (focused) focused.classList.remove("focus");
    focused = el;
    el.classList.add("focus");
    if (el.tagName === "INPUT") el.focus({ preventScroll: true }); else if (document.activeElement && document.activeElement.tagName === "INPUT") document.activeElement.blur();
    if (scroll) scrollIntoView(el);
    prewarm(el);
    sendWs({ type: "view", view: state.view, focus: el.dataset.label || el.textContent.trim().slice(0, 40) });
  };
  const scrollIntoView = (el) => {
    const row = el.closest(".row, .chips, .seasons");
    if (row) { const r = el.getBoundingClientRect(), rr = row.getBoundingClientRect(); if (r.left < rr.left + 20) row.scrollLeft += r.left - rr.left - 20; else if (r.right > rr.right - 20) row.scrollLeft += r.right - rr.right + 20; }
    const cont = el.closest(".list, .scroller, .grid, .panel-inner, .episodes");
    if (cont) { const r = el.getBoundingClientRect(), cr = cont.getBoundingClientRect(); if (r.top < cr.top + 20) cont.scrollTop += r.top - cr.top - 20; else if (r.bottom > cr.bottom - 20) cont.scrollTop += r.bottom - cr.bottom + 20; }
  };
  // Opening DNS + TCP + TLS while the user is still deciding removes ~1 s from the first frame.
  let warmTimer;
  const prewarm = (el) => {
    const id = el && el.dataset && el.dataset.warm;
    clearTimeout(warmTimer);
    if (!id) return;
    warmTimer = setTimeout(() => { post("/player/warm", { channel_id: id }).catch(() => {}); }, 350);
  };

  const center = (el) => { const r = el.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2, r }; };
  const moveFocus = (dir) => {
    const all = focusables();
    if (!focused || !all.includes(focused)) { setFocus(all[0]); return; }
    const c = center(focused);
    let best = null, bestScore = Infinity;
    for (const el of all) {
      if (el === focused) continue;
      const o = center(el); const dx = o.x - c.x, dy = o.y - c.y;
      let primary, secondary;
      if (dir === "ArrowRight") { if (o.r.left < c.r.right - 8) continue; primary = dx; secondary = Math.abs(dy); }
      else if (dir === "ArrowLeft") { if (o.r.right > c.r.left + 8) continue; primary = -dx; secondary = Math.abs(dy); }
      else if (dir === "ArrowDown") { if (o.r.top < c.r.bottom - 8) continue; primary = dy; secondary = Math.abs(dx); }
      else { if (o.r.bottom > c.r.top + 8) continue; primary = -dy; secondary = Math.abs(dx); }
      if (primary < 0) continue;
      const sameRow = !!(focused.closest(".row, .chips, .seasons") && focused.closest(".row, .chips, .seasons") === el.closest(".row, .chips, .seasons"));
      const score = primary + secondary * 2.2 - (sameRow && (dir === "ArrowLeft" || dir === "ArrowRight") ? 200 : 0);
      if (score < bestScore) { bestScore = score; best = el; }
    }
    if (best) setFocus(best);
  };

  // ------------------------------------------------------------------ views plumbing
  const main = $("#main");
  const setNav = () => $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === state.view));
  $$(".nav-item").forEach((b) => b.addEventListener("click", () => go(b.dataset.view)));
  const render = (el, focusSel) => {
    main.replaceChildren(el);
    setNav();
    setFocus((focusSel ? $(focusSel, el) : null) || $(".f", el) || $(".nav-item.active"), false);
    sendWs({ type: "view", view: state.view });
  };
  const go = async (view, arg = null, push = true) => {
    if (push && (view !== state.view || JSON.stringify(arg) !== JSON.stringify(state.viewArg))) state.stack.push({ view: state.view, arg: state.viewArg });
    if (state.stack.length > 20) state.stack.shift();
    state.view = view; state.viewArg = arg;
    closePanel();
    if (!["movies", "series", "home"].includes(view)) setAmbient("");
    const skeleton = SKELETON_FOR[view];
    const skTimer = skeleton ? setTimeout(() => { if (state.view === view) main.replaceChildren(skeletons[skeleton]()); }, 140) : null;
    try { await (views[view] || views.home)(arg); }
    catch (e) { console.error(e); render(h("div", { class: "view" }, h("h1", {}, "Oups"), emptyState("info", "Cette page n'a pas pu se charger", String(e.message || e), h("button", { class: "f btn primary", onclick: () => go(view, arg, false) }, I("refresh"), "Réessayer")))); }
    finally { clearTimeout(skTimer); }
  };
  const back = () => { if ($(".panel")) { closePanel(); return; } const prev = state.stack.pop(); if (prev) go(prev.view, prev.arg, false); else if (state.view !== "home") go("home", null, false); };

  // ------------------------------------------------------------------ shared widgets
  const thumb = (item, cls = "") => {
    const t = h("div", { class: "thumb " + cls });
    if (item.logo) { const img = h("img", { src: item.logo, alt: "", loading: "lazy" }); fitLogo(img, t); img.onerror = () => { img.replaceWith(h("div", { class: "ph" }, initials(item.name))); t.dataset.missing = "1"; }; t.append(img); }
    else { t.append(h("div", { class: "ph" }, initials(item.name))); t.dataset.missing = "1"; }
    return t;
  };
  const channelCard = (c, list) => {
    const card = h("button", { class: "f card", dataset: { label: c.name, warm: c.kind === "live" ? c.id : "" }, onclick: () => playItem(c, list) }, thumb(c),
      h("div", { class: "body" }, h("div", { class: "title" }, c.name), h("div", { class: "sub" }, c.now ? c.now.title : (c.group || "")), c.now ? h("div", { class: "bar" }, h("div", { class: "bar-fill", style: `width:${progressPct(c.now)}%` })) : null),
      c.favorite ? h("span", { class: "fav-star" }, I("star-fill")) : null);
    card.addEventListener("keydown", (e) => { if (e.key === "f" || e.key === "F") toggleFav(c); });
    return card;
  };
  const posterCard = (c) => {
    const card = h("button", { class: "f card poster", dataset: { label: c.name, id: c.id }, onclick: () => openVod(c) }, thumb(c),
      c.resume && c.resume.duration ? h("div", { class: "resume" }, h("i", { style: `width:${(c.resume.position / c.resume.duration) * 100}%` })) : null,
      h("div", { class: "body" }, h("div", { class: "title" }, cleanTitle(c.name)), h("div", { class: "sub" }, ...posterSub(c))));
    return card;
  };
  const posterSub = (c) => {
    const ex = c.extra || {};
    const parts = [ex.year ? h("span", { class: "num" }, String(ex.year)) : null, ex.rating && Number(ex.rating) > 0 ? rating(ex.rating) : null].filter(Boolean);
    return parts.length ? parts.flatMap((p, i) => (i ? [" · ", p] : [p])) : [groupLabel(c.group)];
  };
  const rowOf = (items, maker) => h("div", { class: "row" }, items.map((it, i) => stagger(maker(it), i)));
  const channelRow = (c, list) => {
    const row = h("button", { class: "f item", dataset: { label: c.name, warm: c.kind === "live" ? c.id : "" }, onclick: () => (c.kind === "live" ? playItem(c, list) : openVod(c)) },
      c.logo ? h("img", { class: "logo", src: c.logo, alt: "", loading: "lazy", onerror: (e) => (e.target.style.visibility = "hidden") }) : h("div", { class: "logo" }),
      h("div", { class: "grow" }, h("div", { class: "name" }, c.name), h("div", { class: "sub" }, c.now ? `${c.now.title}${c.next ? "  ·  Ensuite : " + c.next.title : ""}` : c.group)),
      c.now ? h("div", { class: "bar" }, h("div", { class: "bar-fill", style: `width:${progressPct(c.now)}%` })) : null,
      c.favorite ? h("span", { class: "badge soon icon" }, I("star-fill")) : null);
    row.addEventListener("keydown", (e) => { if (e.key === "f" || e.key === "F") toggleFav(c); });
    return row;
  };
  const toggleFav = async (c) => { try { const r = await post("/favorites/" + c.id); c.favorite = r.favorite; toast(r.favorite ? "Ajouté aux favoris" : "Retiré des favoris"); } catch (e) { toast(e.message, true); } };
  const appCard = (a) => h("button", { class: "f card app", dataset: { label: a.name }, onclick: () => openApp(a) }, h("div", { class: "thumb", style: `background:${a.color}` }, a.name), h("div", { class: "body" }, h("div", { class: "title" }, a.name), h("div", { class: "sub" }, "App")));
  const openApp = async (a) => { try { await post("/apps/" + a.key); } catch (e) { toast(e.message, true); } };
  const backfillPosters = async (container) => {
    const missing = $$(".card.poster .thumb[data-missing] ", container).map((t) => t.closest(".card").dataset.id).filter(Boolean).slice(0, 24);
    if (!missing.length) return;
    try {
      const r = await post("/vod/posters", { ids: missing });
      for (const [id, url] of Object.entries(r.posters)) { const card = $(`.card.poster[data-id="${id}"]`, container); if (card) { const t = $(".thumb", card); const img = h("img", { src: url, alt: "" }); fitLogo(img, t); t.replaceChildren(img); delete t.dataset.missing; } }
    } catch (_) { /* optional */ }
  };
  const drawQr = (text) => { const el = $("#qr"); if (!el || !window.qrcode || !text) return; try { const q = window.qrcode(0, "M"); q.addData(text); q.make(); el.innerHTML = q.createSvgTag({ cellSize: 6, margin: 0, scalable: true }); } catch (e) { el.textContent = text; } };

  // Local library (tv-library.js): films and series found on the drives. Late helpers are wrapped so they resolve at call time.
  const library = window.AuraTVLibrary({
    $, $$, h, I, api, post, toast, emptyState, stagger, fmtDur, fmtMin, rating, state,
    go: (...a) => go(...a), render: (...a) => render(...a), setFocus: (...a) => setFocus(...a), setAmbient: (...a) => setAmbient(...a),
    panel: (...a) => panel(...a), closePanel: () => closePanel(), play: (...a) => beginBrowserPlayback(...a), external: (...a) => showExternal(...a),
  });

  // ------------------------------------------------------------------ views
  const views = {
    async home() {
      const d = await api("/home");
      const localTitles = d.library ? d.library.counts.films + d.library.counts.series + d.library.counts.links : 0;
      if (!d.configured && !localTitles) { state.setupMode = true; return views.setup(); }
      state.setupMode = false;
      const v = h("div", { class: "view" }, h("h1", {}, "Accueil"));
      const sc = h("div", { class: "scroller" }); v.append(sc);
      const evCard = (ev, k) => ev ? h("button", { class: "f hero-card", dataset: { label: ev.name }, onclick: () => openEvent(ev) }, h("span", { class: "wm" }, I(sportIcon(ev.sport))), h("div", { class: "k" }, ev.status === "live" ? h("span", { class: "live-dot" }) : null, k, ev.status === "live" ? " · en direct" : ""), h("div", { class: "t" }, ev.name), h("div", { class: "d" }, ev.session, " · ", ev.start ? h("span", { class: "num" }, fmtDate(ev.start) + " " + fmtTime(ev.start)) : "date à confirmer")) : h("div", { class: "hero-card" }, h("div", { class: "k" }, k), h("div", { class: "t dim" }, "Aucun événement"));
      sc.append(h("div", { class: "hero" }, evCard(d.next_f1, "Prochaine F1"), evCard(d.next_ufc, "Prochain UFC")));
      sc.append(...library.homeRows(d.library || {}));
      if (d.live_sports.length) { sc.append(h("h2", {}, "Sport en direct")); sc.append(rowOf(d.live_sports, (ev) => h("button", { class: "f card", onclick: () => openEvent(ev) }, h("div", { class: "thumb" }, h("div", { class: "sport-thumb" }, I(sportIcon(ev.sport)), h("span", {}, sportLabel(ev.sport)))), h("div", { class: "body" }, h("div", { class: "title" }, ev.name), h("div", { class: "sub" }, ev.session))))); }
      if (d.history.length) { sc.append(h("h2", {}, "Reprendre")); sc.append(rowOf(d.history, (hst) => { const it = { id: hst.channel_id, name: hst.name, url: hst.url, kind: hst.kind, logo: hst.logo, extra: hst.extra, group: hst.group }; return h("button", { class: "f card", dataset: { label: hst.name }, onclick: () => (hst.kind === "live" ? playItem(it, []) : openVod(it)) }, thumb(hst), h("div", { class: "body" }, h("div", { class: "title" }, cleanTitle(hst.name)), h("div", { class: "sub" }, hst.kind === "live" ? "Direct" : (hst.duration ? fmtDur(hst.position) + " / " + fmtDur(hst.duration) : "Vidéo")), hst.duration ? h("div", { class: "bar" }, h("div", { class: "bar-fill", style: `width:${(hst.position / hst.duration) * 100}%` })) : null)); })); }
      if (d.favorites.length) { sc.append(h("h2", {}, "Favoris")); sc.append(rowOf(d.favorites, (c) => channelCard(c, d.favorites))); }
      if (d.movies.length) { sc.append(h("h2", {}, "Films")); sc.append(rowOf(d.movies.slice(0, 20), posterCard)); }
      if (d.series.length) { sc.append(h("h2", {}, "Séries")); sc.append(rowOf(d.series.slice(0, 20), posterCard)); }
      sc.append(h("h2", {}, "Apps")); sc.append(rowOf(d.apps, appCard));
      render(v);
      backfillPosters(sc);
    },

    async live(arg) {
      const group = arg && arg.group !== undefined ? arg.group : (state.last.liveGroup || null);
      const d = await api("/channels?kind=live&limit=400" + (group ? "&group=" + encodeURIComponent(group) : ""));
      const v = h("div", { class: "view" }, h("h1", {}, "Direct"));
      const left = h("div", { class: "list" });
      left.append(h("button", { class: "f item group" + (!group ? " active" : ""), onclick: () => { state.last.liveGroup = null; go("live", { group: null }, false); } }, h("span", {}, "Toutes"), h("span", { class: "count" }, "")));
      d.groups.forEach((g) => left.append(h("button", { class: "f item group" + (g.name === group ? " active" : ""), dataset: { label: g.name }, onclick: () => { state.last.liveGroup = g.name; go("live", { group: g.name }, false); } }, h("span", {}, groupLabel(g.name)), h("span", { class: "count num" }, g.count))));
      const right = h("div", { class: "list" });
      if (!d.items.length) right.append(emptyState("tv", "Aucune chaîne ici", "Ajoute une source IPTV depuis Réglages ou le téléphone. Les chaînes gratuites s'installent en un geste.", h("button", { class: "f btn primary", onclick: () => go("wizard") }, I("plus"), "Ajouter une source")));
      d.items.forEach((c, i) => right.append(stagger(channelRow(c, d.items), i)));
      v.append(h("div", { class: "split" }, left, right));
      render(v, group ? ".list:nth-child(2) .item" : null);
    },

    async favorites() {
      const d = await api("/favorites");
      const v = h("div", { class: "view" }, h("h1", {}, "Favoris"));
      const list = h("div", { class: "list scroller" });
      if (!d.items.length) list.append(emptyState("star", "Pas encore de favoris", h("span", {}, "Sur une chaîne, appuie sur ", h("span", { class: "kbd" }, "F"), " ou touche l'étoile sur le téléphone.")));
      d.items.forEach((c, i) => list.append(stagger(channelRow(c, d.items), i)));
      v.append(list); render(v);
    },

    async sports(arg) { return sportsView(arg && arg.sport ? arg.sport : "all"); },
    async f1() { return sportsView("f1"); },
    async ufc() { return sportsView("ufc"); },
    async library(arg) { return library.view(arg); },
    async movies(arg) { return vodView("vod", arg); },
    async series(arg) { return vodView("series", arg); },

    async apps() {
      const d = await api("/apps");
      const v = h("div", { class: "view" }, h("h1", {}, "Apps"));
      if (!d.kiosk) v.append(h("div", { class: "muted", style: "margin-bottom:16px" }, "Navigateur kiosque non détecté : les apps s'ouvrent uniquement sur le boîtier."));
      v.append(h("div", { class: "grid scroller" }, d.apps.map((a, i) => stagger(appCard(a), i))));
      v.append(h("div", { class: "hint" }, "Dans une app, le téléphone sert de trackpad et de clavier. Bouton Accueil pour revenir."));
      render(v);
    },

    async search(arg) {
      const v = h("div", { class: "view" }, h("h1", {}, "Recherche"));
      const input = h("input", { class: "f input", placeholder: "Chaîne, film, série, événement…", value: (arg && arg.q) || "" });
      const results = h("div", { class: "scroller", style: "margin-top:20px" });
      let timer;
      const run = async () => {
        const q = input.value.trim(); if (q.length < 2) { results.replaceChildren(); return; }
        const d = await api("/search?q=" + encodeURIComponent(q)); results.replaceChildren();
        if (d.live.length) { results.append(h("h2", {}, "Chaînes")); results.append(rowOf(d.live, (c) => channelCard(c, d.live))); }
        if (d.vod.length) { results.append(h("h2", {}, "Films")); results.append(rowOf(d.vod, posterCard)); }
        if (d.series.length) { results.append(h("h2", {}, "Séries")); results.append(rowOf(d.series, posterCard)); }
        if (d.sports.length) { results.append(h("h2", {}, "Événements")); results.append(rowOf(d.sports, (ev) => h("button", { class: "f card", onclick: () => openEvent(ev) }, h("div", { class: "thumb" }, h("div", { class: "sport-thumb" }, I(sportIcon(ev.sport)), h("span", {}, sportLabel(ev.sport)))), h("div", { class: "body" }, h("div", { class: "title" }, ev.name), h("div", { class: "sub" }, ev.start ? fmtDate(ev.start) : ""))))); }
        if (d.apps.length) { results.append(h("h2", {}, "Apps")); results.append(rowOf(d.apps, appCard)); }
        if (!results.children.length) results.append(emptyState("search", "Rien trouvé", "Essaie un autre mot : nom de chaîne, titre de film, équipe ou compétition."));
        backfillPosters(results);
      };
      input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(run, 300); });
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); run().then(() => { const f = $(".f", results); if (f) setFocus(f); }); } });
      v.append(input, results); render(v); if (input.value) run();
    },

    async settings() {
      const [net, sys, setup] = await Promise.all([api("/network"), api("/system"), api("/setup")]);
      const v = h("div", { class: "view" }, h("h1", {}, "Réglages"));
      const sc = h("div", { class: "scroller" });
      sc.append(h("h2", {}, "Configurer depuis un téléphone ou un PC"));
      sc.append(h("div", { class: "setup" }, h("div", { class: "box" }, h("div", { class: "muted" }, "Sur le même réseau, ouvre :"), ...net.urls.map((u, i) => h("div", { class: "url" + (i ? " small" : "") }, u))), h("div", { id: "qr" })));
      sc.append(h("div", { style: "margin-top:14px" },
        h("button", { class: "f btn primary", onclick: () => go("wizard") }, I("compass"), "Assistant de configuration"),
        h("button", { class: "f btn", onclick: () => wifiWizard() }, I("wifi"), "Wi-Fi"),
        h("button", { class: "f btn", onclick: async () => { toast("Rafraîchissement…"); try { await post("/refresh"); toast("Sources, guide et sports à jour"); } catch (e) { toast(e.message, true); } } }, I("refresh"), "Rafraîchir les sources")));
      sc.append(h("h2", {}, "Réseau"));
      sc.append(h("div", { class: "kv" }, h("div", { class: "k" }, "État"), h("div", {}, net.online ? "Connecté" : "Hors ligne"), h("div", { class: "k" }, "Wi-Fi"), h("div", {}, net.wifi_ssid || "—"), h("div", { class: "k" }, "Adresses IP"), h("div", {}, net.addresses.join(", ") || "—"), h("div", { class: "k" }, "Interfaces"), h("div", {}, net.interfaces.map((i) => `${i.device} (${i.type}: ${i.state})`).join(" · ") || "—")));
      sc.append(h("h2", {}, "Système"));
      sc.append(h("div", { class: "kv" }, h("div", { class: "k" }, "Version"), h("div", {}, "Aura " + sys.version), h("div", { class: "k" }, "Machine"), h("div", {}, sys.model || sys.platform), h("div", { class: "k" }, "Catalogue IPTV"), h("div", {}, `${setup.counts.live} chaînes · ${setup.counts.vod} films · ${setup.counts.series} séries`), h("div", { class: "k" }, "Sur les disques"), h("div", {}, `${setup.library.films} films · ${setup.library.series} séries · ${setup.library.episodes} épisodes`), h("div", { class: "k" }, "Disque libre"), h("div", {}, Math.round(sys.disk_free / 1e9) + " Go"), h("div", { class: "k" }, "Lecteur mpv"), h("div", {}, sys.mpv ? "présent" : "absent")));
      sc.append(h("div", { style: "margin-top:14px" }, h("button", { class: "f btn", onclick: () => confirmAction("Redémarrer le boîtier ?", () => post("/system/reboot")) }, I("restart"), "Redémarrer"), h("button", { class: "f btn", onclick: () => confirmAction("Éteindre le boîtier ?", () => post("/system/shutdown")) }, I("power"), "Éteindre")));
      v.append(sc); render(v); drawQr(`${net.urls[1] || net.urls[0]}remote/`);
    },

    async setup() {
      const [net] = await Promise.all([api("/network")]);
      const v = h("div", { class: "view" }, h("h1", {}, "Bienvenue"));
      const box = h("div", { class: "box" });
      if (!net.online) {
        box.append(h("h2", {}, "1. Connecter le boîtier à Internet"));
        box.append(h("ol", {}, h("li", {}, "Branche un câble Ethernet (le plus simple), ou"), h("li", {}, "Avec un clavier ou une souris : ", h("button", { class: "f btn primary", onclick: () => wifiWizard() }, "choisir un Wi-Fi")),
          net.hotspot_active ? h("li", {}, `Ou connecte ton téléphone au Wi-Fi « ${net.hotspot_ssid} » (mot de passe ${net.hotspot_password}) puis ouvre http://10.42.0.1:${location.port || 80}/`) : null));
      } else {
        box.append(h("h2", {}, "Le boîtier est en ligne"));
        box.append(h("div", { class: "muted" }, "Sur ton téléphone ou PC (même réseau), ouvre :"));
        net.urls.forEach((u, i) => box.append(h("div", { class: "url" + (i ? " small" : "") }, u)));
        box.append(h("div", { class: "muted", style: "margin:14px 0 6px" }, "Ou configure directement ici avec un clavier / une souris :"));
        box.append(h("button", { class: "f btn primary", onclick: () => go("wizard") }, I("compass"), "Configurer à l'écran"));
      }
      v.append(h("div", { class: "setup" }, box, h("div", { id: "qr" })));
      v.append(h("div", { class: "hint" }, "Cette page se met à jour automatiquement."));
      render(v); if (net.online) drawQr(`${net.urls[1] || net.urls[0]}remote/`);
    },

    async wizard() { return wizardView(); },
  };

  // ------------------------------------------------------------------ on-screen setup wizard (mouse / keyboard)
  const wizardView = async () => {
    const setup = await api("/setup");
    let step = setup.network_online ? 1 : 0;
    const steps = ["Réseau", "Source IPTV", "Guide TV", "Terminé"];
    const v = h("div", { class: "view wizard" });
    const draw = async () => {
      v.replaceChildren(h("h1", {}, "Configuration"), h("div", { class: "steps" }, steps.map((_, i) => h("span", { class: i <= step ? "on" : "" }))), h("h2", {}, `Étape ${step + 1} / ${steps.length} · ${steps[step]}`));
      const box = h("div", { class: "box" }); v.append(box);
      if (step === 0) {
        const net = await api("/network");
        box.append(h("p", { class: "muted" }, net.online ? `En ligne (${net.addresses.join(", ")}).` : "Pas d'accès Internet : branche un câble Ethernet ou choisis un Wi-Fi."));
        box.append(h("button", { class: "f btn", onclick: () => wifiWizard(() => draw()) }, "Choisir un Wi-Fi"), h("button", { class: "f btn primary", onclick: () => { step = 1; draw(); } }, "Continuer"));
      } else if (step === 1) {
        box.append(sourceForm(() => { step = 2; draw(); }), h("button", { class: "f btn ghost", onclick: () => { step = 2; draw(); } }, "Passer"));
      } else if (step === 2) {
        const d = await api("/epg/sources");
        box.append(h("p", { class: "muted" }, "Le guide TV affiche « en ce moment / ensuite » et sert à trouver les chaînes d'un événement. Les sources Xtream ajoutent souvent le leur."));
        box.append(...d.free.slice(0, 3).map((f) => h("button", { class: "f btn", onclick: async (e) => { const btn = e.currentTarget; try { await post("/epg/sources", { key: f.key }); btn.replaceChildren(I("check"), f.name); } catch (err) { toast(err.message, true); } } }, I("plus"), f.name)));
        box.append(h("div", {}, h("button", { class: "f btn primary", onclick: () => { step = 3; draw(); } }, "Continuer")));
      } else {
        box.append(h("p", {}, "C'est prêt. Les chaînes, films et séries se chargent en arrière-plan. Une clé TMDB (gratuite) ajoutera affiches et résumés : Réglages sur le téléphone › Métadonnées."));
        box.append(h("button", { class: "f btn primary", onclick: async () => { await api("/settings", { method: "PUT", body: { values: { setup_done: "1" } } }); state.setupMode = false; go("home", null, false); } }, "Terminer"));
      }
      setFocus($(".f", box) || $(".nav-item.active"), false);
    };
    render(v); await draw();
  };
  const field = (label, attrs) => { const i = h("input", { class: "f input", autocomplete: "off", ...attrs }); return { el: h("div", { class: "field" }, h("label", {}, label), i), input: i }; };
  const sourceForm = (onAdded) => {
    const wrap = h("div");
    const seg = h("div", { class: "seg" });
    const area = h("div");
    const busy = async (btn, fn) => {
      const kids = [...btn.childNodes];
      const error = btn.parentElement ? btn.parentElement.querySelector(".form-error") : null;
      btn.replaceChildren("…");
      if (error) error.textContent = "";
      try { await fn(); } catch (e) { if (error) error.textContent = e.message; else toast(e.message, true); } finally { btn.replaceChildren(...kids); }
    };
    const forms = {
      xtream: () => { const url = field("Adresse du serveur (http://hôte:port)", { placeholder: "http://exemple.tv:8080" }), u = field("Identifiant", {}), p = field("Mot de passe", { type: "password" }); return h("div", {}, h("p", { class: "muted" }, "Ton fournisseur t'a donné une adresse + identifiant + mot de passe (Xtream Codes). Live, films, séries et guide arrivent d'un coup."), url.el, u.el, p.el, h("button", { class: "f btn primary", onclick: (e) => busy(e.currentTarget, async () => { const s = await post("/sources", { type: "xtream", url: url.input.value, username: u.input.value, password: p.input.value }); toast(`${s.name} : ${s.item_count} éléments`); onAdded(); }) }, I("link"), "Connecter"), h("div", { class: "form-error" })); },
      m3u: () => { const url = field("Lien M3U", { placeholder: "https://…/playlist.m3u" }), epg = field("Lien EPG XMLTV (optionnel)", {}); return h("div", {}, url.el, epg.el, h("button", { class: "f btn primary", onclick: (e) => busy(e.currentTarget, async () => { const s = await post("/sources", { type: "m3u_url", url: url.input.value, epg_url: epg.input.value }); toast(`${s.name} : ${s.item_count} éléments`); onAdded(); }) }, I("plus"), "Ajouter"), h("div", { class: "form-error" })); },
      free: () => { const b2 = h("div", {}, h("p", { class: "muted" }, "Chaînes publiques gratuites et films libres de droits. Qualité variable, zéro compte.")); api("/sources").then((d) => d.bundles.forEach((bd) => b2.append(h("button", { class: "f btn", onclick: (e) => { const btn = e.currentTarget; busy(btn, async () => { const s = await post("/sources", { type: "free", bundle: bd.key }); btn.dataset.done = `${s.name} (${s.item_count})`; }).then(() => { if (btn.dataset.done) btn.replaceChildren(I("check"), btn.dataset.done); }); } }, I("plus"), bd.name)))); b2.append(h("div", {}, h("button", { class: "f btn primary", onclick: onAdded }, "Continuer"))); return b2; },
    };
    const set = (k) => { $$(".tab", seg).forEach((b) => b.classList.toggle("active", b.dataset.k === k)); area.replaceChildren(forms[k]()); setFocus($(".f", area) || $(".tab", seg), false); };
    [["xtream", "Xtream"], ["m3u", "M3U"], ["free", "Gratuit"]].forEach(([k, l]) => seg.append(h("button", { class: "f tab", "data-k": k, onclick: () => set(k) }, l)));
    wrap.append(seg, area); set("xtream"); return wrap;
  };

  // ------------------------------------------------------------------ sports
  const sportsView = async (sport) => {
    const d = await api("/sports" + (sport !== "all" ? "?sport=" + sport : ""));
    const titles = { all: "Sports", football: "Football", f1: "Formule 1", ufc: "UFC / MMA", basketball: "Basket", boxing: "Boxe", other: "Autres sports" };
    const v = h("div", { class: "view" }, h("h1", {}, titles[sport] || "Sports"));
    v.append(h("div", { class: "tabs" }, ["all", "football", "f1", "ufc", "basketball", "boxing", "other"].map((s) => h("button", { class: "f tab" + (s === sport ? " active" : ""), onclick: () => go(s === "f1" || s === "ufc" ? s : "sports", { sport: s }, false) }, titles[s]))));
    const list = h("div", { class: "list", style: "height:calc(100% - 140px)" });
    if (!d.events.length) list.append(emptyState("calendar", "Aucun événement à venir", "Le calendrier se met à jour tout seul dès que le boîtier est en ligne."));
    let lastDay = "";
    d.events.forEach((ev, n) => { const day = ev.start ? fmtDate(ev.start) : "À confirmer"; if (day !== lastDay) { list.append(h("h2", {}, day)); lastDay = day; } list.append(stagger(eventRow(ev), n)); });
    v.append(list); render(v, ".event");
  };
  const eventRow = (ev) => h("button", { class: "f event", dataset: { label: ev.name }, onclick: () => openEvent(ev) }, h("div", { class: "when" }, ev.start ? fmtTime(ev.start) : "—", h("small", {}, I(sportIcon(ev.sport)), sportLabel(ev.sport))), h("div", {}, h("div", { class: "name" }, ev.name), h("div", { class: "loc" }, [ev.session, ev.location].filter(Boolean).join(" · "))), h("span", { class: "badge " + (ev.status === "live" ? "live" : ev.status === "past" ? "past" : "soon") }, ev.status === "live" ? "En direct" : ev.status === "past" ? "Terminé" : ev.status === "tba" ? "À venir" : "Programmé"));
  const openEvent = async (ev) => {
    const p = panel(ev.name, `${ev.session}${ev.location ? " · " + ev.location : ""}${ev.start ? " · " + fmtDate(ev.start) + " " + fmtTime(ev.start) : ""}`);
    const body = h("div", { class: "list", style: "height:calc(100% - 120px)" }, skeletonItems(4)); p.append(body);
    try { const d = await api("/sports/" + ev.id + "/streams"); body.replaceChildren(); if (!d.streams.length) body.append(emptyState("tv", "Aucune chaîne trouvée", "Aucune chaîne de tes sources ne semble diffuser cet événement. Une source avec des chaînes sport élargit la recherche.")); else body.append(h("h2", {}, "Chaînes probables (meilleure en premier)")); d.streams.forEach((c) => body.append(channelRow(c, d.streams))); setFocus($(".f", body) || $(".close", p.parentElement)); }
    catch (e) { body.replaceChildren(h("div", { class: "empty" }, e.message)); }
  };

  // ------------------------------------------------------------------ movies / series
  const vodView = async (kind, arg) => {
    const isBrowse = arg && arg.browse;
    const title = kind === "vod" ? "Films" : "Séries";
    if (!isBrowse) {
      const d = await api("/vod/home?kind=" + kind);
      const v = h("div", { class: "view" });
      const sc = h("div", { class: "scroller", style: "height:100%" }); v.append(sc);
      if (!d.total) {
        sc.append(h("h1", {}, title));
        sc.append(emptyState(kind === "vod" ? "film" : "stack", kind === "vod" ? "Aucun film pour l'instant" : "Aucune série pour l'instant",
          kind === "vod" ? "Les films arrivent avec ta source IPTV : un compte Xtream ou une playlist M3U avec vidéo à la demande. Sans abonnement, la sélection de classiques libres de droits se lit tout de suite." : "Les séries arrivent avec un compte Xtream ou une playlist M3U qui contient une section séries.",
          h("button", { class: "f btn primary", onclick: () => go("wizard") }, I("plus"), "Ajouter une source"),
          kind === "vod" ? h("button", { class: "f btn", onclick: async (e) => { const btn = e.currentTarget; btn.replaceChildren("Chargement…"); try { const s = await post("/sources", { type: "free", bundle: "archive-films" }); toast(`${s.item_count} films ajoutés`); go("movies", null, false); } catch (err) { btn.replaceChildren(I("plus"), "Films classiques gratuits"); toast(err.message, true); } } }, I("plus"), "Films classiques gratuits") : null));
        render(v); return;
      }
      if (d.hero) {
        const hero = d.hero; const ex = hero.extra || {};
        setAmbient(hero.logo);
        const b = h("button", { class: "f banner", dataset: { label: hero.name }, onclick: () => openVod(hero) },
          h("div", { class: "bg", style: hero.logo ? `background-image:url("${hero.logo}")` : "" }), h("div", { class: "veil" }),
          h("div", { class: "txt" }, h("div", { class: "k" }, title + " à la une"), h("div", { class: "t" }, cleanTitle(hero.name)),
            h("div", { class: "meta" }, ex.year ? h("span", { class: "badge pill num" }, String(ex.year)) : null, ex.rating && Number(ex.rating) > 0 ? h("span", { class: "badge pill" }, rating(ex.rating)) : null, hero.group ? h("span", { class: "badge pill" }, groupLabel(hero.group)) : null),
            h("div", { class: "d" }, ex.plot || "")),
          h("div", { class: "art" }, hero.logo ? h("img", { src: hero.logo, alt: "" }) : null));
        sc.append(b);
      } else sc.append(h("h1", {}, title));
      sc.append(h("div", { class: "chips" }, h("button", { class: "f chip active" }, `${d.total} ${kind === "vod" ? "films" : "séries"}`), h("button", { class: "f chip", onclick: () => go(kind === "vod" ? "movies" : "series", { browse: true, sort: "added" }, true) }, I("grid"), "Parcourir tout"), h("button", { class: "f chip", onclick: () => go("search") }, I("search"), "Rechercher"), ...d.groups.slice(0, 8).map((g) => h("button", { class: "f chip", onclick: () => go(kind === "vod" ? "movies" : "series", { browse: true, group: g.name }, true) }, groupLabel(g.name)))));
      d.rows.forEach((row) => { sc.append(h("h2", {}, groupLabel(row.title), row.count ? h("span", { class: "count num" }, String(row.count)) : null)); sc.append(rowOf(row.items, posterCard)); });
      render(v, ".banner, .chip"); backfillPosters(sc); return;
    }
    // browse grid
    const group = arg.group || null, q = arg.q || "", sort = arg.sort || "default", offset = arg.offset || 0;
    const d = await api(`/vod/browse?kind=${kind}&limit=60&offset=${offset}&sort=${sort}` + (group ? "&group=" + encodeURIComponent(group) : "") + (q ? "&q=" + encodeURIComponent(q) : ""));
    const v = h("div", { class: "view" }, h("h1", {}, group ? groupLabel(group) : title, h("span", { class: "count num" }, String(d.total))));
    const left = h("div", { class: "list" });
    const input = h("input", { class: "f input", placeholder: "Filtrer…", value: q, style: "font-size:18px;padding:10px 14px;margin-bottom:10px" });
    let timer; input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => go(kind === "vod" ? "movies" : "series", { ...arg, q: input.value.trim(), offset: 0 }, false), 400); });
    left.append(input);
    left.append(h("div", { class: "seg", style: "margin:0 0 10px" }, [["added", "Récents"], ["rating", "Notés"], ["name", "A à Z"]].map(([k, l]) => h("button", { class: "f tab" + (sort === k ? " active" : ""), onclick: () => go(kind === "vod" ? "movies" : "series", { ...arg, sort: k, offset: 0 }, false) }, l))));
    left.append(h("button", { class: "f item group" + (!group ? " active" : ""), onclick: () => go(kind === "vod" ? "movies" : "series", { ...arg, group: null, offset: 0 }, false) }, "Tout"));
    d.groups.forEach((g) => left.append(h("button", { class: "f item group" + (g.name === group ? " active" : ""), dataset: { label: g.name }, onclick: () => go(kind === "vod" ? "movies" : "series", { ...arg, group: g.name, offset: 0 }, false) }, h("span", {}, groupLabel(g.name)), h("span", { class: "count num" }, g.count))));
    const grid = h("div", { class: "grid list" });
    if (!d.items.length) { const es = emptyState("search", "Aucun titre", "Aucun titre ne correspond à ce filtre."); es.style.gridColumn = "1 / -1"; grid.append(es); }
    d.items.forEach((c, i) => grid.append(stagger(posterCard(c), i)));
    if (offset + d.items.length < d.total) grid.append(h("button", { class: "f card poster more", onclick: () => go(kind === "vod" ? "movies" : "series", { ...arg, offset: offset + 60 }, false) }, h("div", { class: "thumb" }, I("arrow-right"), "Suite"), h("div", { class: "body" }, h("div", { class: "title" }, `${offset + 61}–${Math.min(d.total, offset + 120)}`))));
    if (offset > 0) grid.prepend(h("button", { class: "f card poster more", onclick: () => go(kind === "vod" ? "movies" : "series", { ...arg, offset: Math.max(0, offset - 60) }, false) }, h("div", { class: "thumb" }, I("arrow-left"), "Précédent"), h("div", { class: "body" }, h("div", { class: "title" }, "Page précédente"))));
    v.append(h("div", { class: "split" }, left, grid));
    render(v, ".grid .f"); backfillPosters(grid);
  };

  const openVod = async (c) => {
    const p = panel(cleanTitle(c.name), c.group, c.logo);
    const posterEl = h("img", { class: "poster-big", src: c.logo || "", alt: "" });
    const info = h("div", {});
    const meta = h("div", { class: "meta" });
    const cast = h("div", { class: "cast" });
    const synopsis = h("p", {}, (c.extra && c.extra.plot) || "");
    const actions = h("div", { class: "actions" });
    info.append(meta, synopsis, cast, actions);
    const wrap = h("div", { class: "detail" }, h("div", {}, posterEl), info);
    p.append(wrap);
    const extra = h("div", {}); p.append(extra);
    const isSeries = c.kind === "series";
    const playBtn = h("button", { class: "f btn primary", onclick: () => playItem(c, []) }, I("play"), "Lire");
    if (!isSeries) actions.append(playBtn);
    actions.append(h("button", { class: "f btn", onclick: () => toggleFav(c) }, I("star"), "Favori"));
    setFocus($(".f", actions));
    try {
      const d = await api("/vod/" + c.id);
      const m = d.meta;
      $("h1", p).textContent = m.title || cleanTitle(c.name);
      if (m.poster) posterEl.src = m.poster;
      if (m.backdrop || m.poster) { $(".backdrop", p.parentElement).style.backgroundImage = `url("${m.backdrop || m.poster}")`; setAmbient(m.backdrop || m.poster); }
      meta.replaceChildren(...[m.year ? String(m.year) : "", m.runtime ? fmtMin(m.runtime) : "", m.rating && Number(m.rating) > 0 ? rating(m.rating) : "", ...(m.genres || []).slice(0, 4)].filter(Boolean).map((x) => h("span", { class: "badge pill" }, x)));
      if (m.overview) synopsis.textContent = m.overview;
      if (m.cast && m.cast.length) cast.textContent = (m.director ? "Réalisé par " + m.director + " · " : "") + "Avec " + m.cast.slice(0, 6).join(", ");
      else if (m.director) cast.textContent = "Réalisé par " + m.director;
      if (d.resume && d.resume.duration && d.resume.position > 30) actions.prepend(h("button", { class: "f btn primary", onclick: () => playItem(c, [], false, d.resume.position) }, I("play"), `Reprendre à ${fmtDur(d.resume.position)}`));
      if (m.trailer) actions.append(h("button", { class: "f btn", onclick: () => post("/apps/trailer", { url: m.trailer }).catch((e) => toast(e.message, true)) }, I("clapper"), "Bande-annonce"));
      if (isSeries) {
        const eps = d.episodes || [];
        if (!eps.length) extra.append(emptyState("stack", "Aucun épisode", "Le fournisseur n'a pas encore publié d'épisode pour cette série."));
        else {
          const seasons = [...new Set(eps.map((e) => e.season))];
          const list = h("div", { class: "episodes list" });
          const showSeason = (s) => {
            $$(".seasons .tab", extra).forEach((t) => t.classList.toggle("active", String(t.dataset.s) === String(s)));
            list.replaceChildren();
            const items = eps.filter((e) => e.season === s).map((e, i) => ({ id: `${c.id}-s${s}e${e.episode || i + 1}`, name: `${cleanTitle(c.name)} · S${String(s).padStart(2, "0")}E${String(e.episode || i + 1).padStart(2, "0")}`, title: e.title, url: e.url, kind: "vod", logo: e.image || c.logo, extra: {} }));
            items.forEach((it, i) => list.append(h("button", { class: "f item", dataset: { label: it.name }, onclick: () => playItem(it, items) }, it.logo ? h("img", { class: "ep-img", src: it.logo, alt: "", loading: "lazy", onerror: (e) => (e.target.style.visibility = "hidden") }) : h("div", { class: "ep-img" }), h("div", { class: "grow" }, h("div", { class: "name" }, `${i + 1}. ${it.title}`), h("div", { class: "sub" }, eps.find((e) => e.url === it.url).plot || (eps.find((e) => e.url === it.url).duration ? "Durée " + eps.find((e) => e.url === it.url).duration : ""))))));
          };
          extra.append(h("div", { class: "seasons" }, seasons.map((s) => h("button", { class: "f tab", "data-s": s, onclick: () => showSeason(s) }, "Saison " + s))), list);
          showSeason(seasons[0]);
        }
      }
      if (d.similar && d.similar.length) { extra.append(h("h2", {}, "Dans le même genre")); extra.append(rowOf(d.similar, posterCard)); backfillPosters(extra); }
    } catch (e) { toast(e.message, true); }
  };

  // ------------------------------------------------------------------ panels
  const panel = (title, sub, backdrop) => {
    closePanel();
    const inner = h("div", { class: "panel-inner list" }, h("button", { class: "f close", onclick: closePanel }, I("close"), "Fermer"), h("h1", {}, title), sub ? h("div", { class: "muted", style: "margin:-4px 0 18px" }, sub) : null);
    const p = h("div", { class: "panel" }, h("div", { class: "backdrop", style: backdrop ? `background-image:url("${backdrop}")` : "" }), h("div", { class: "veil" }), inner);
    main.append(p);
    return inner;
  };
  const closePanel = () => { const p = $(".panel"); if (p) { p.remove(); setFocus($(".f", main) || $(".nav-item.active"), false); } };
  const confirmAction = (msg, fn) => { const p = panel(msg, ""); p.append(h("div", {}, h("button", { class: "f btn danger", onclick: async () => { try { await fn(); toast("OK"); } catch (e) { toast(e.message, true); } closePanel(); } }, "Confirmer"), h("button", { class: "f btn", onclick: closePanel }, "Annuler"))); setFocus($(".btn", p)); };
  const wifiWizard = async (onDone) => {
    const p = panel("Wi-Fi", "Choisis un réseau (clavier requis pour le mot de passe)");
    const body = h("div", { class: "list", style: "height:calc(100% - 120px)" }, skeletonItems(4)); p.append(body);
    try { const d = await api("/network/wifi"); body.replaceChildren(); if (!d.networks.length) body.append(emptyState("wifi", "Aucun réseau détecté", "Vérifie que la carte Wi-Fi est reconnue, ou branche un câble Ethernet.")); d.networks.forEach((n) => body.append(h("button", { class: "f item", dataset: { label: n.ssid }, onclick: () => wifiPassword(n, onDone) }, h("div", { class: "grow" }, h("div", { class: "name" }, n.ssid), h("div", { class: "sub" }, `${n.security || "ouvert"} · signal ${n.signal}%${n.active ? " · connecté" : ""}`))))); setFocus($(".f", body) || $(".close", p)); }
    catch (e) { body.replaceChildren(h("div", { class: "empty" }, e.message)); }
  };
  const wifiPassword = (n, onDone) => {
    const p = panel(n.ssid, n.security ? "Mot de passe du réseau" : "Réseau ouvert");
    const input = h("input", { class: "f input", type: "text", placeholder: "Mot de passe", autocomplete: "off" });
    const connect = async () => { toast("Connexion à " + n.ssid + "…"); try { const r = await post("/network/wifi", { ssid: n.ssid, password: input.value }); toast(r.message || "Connecté"); closePanel(); if (onDone) onDone(); else setTimeout(() => go(state.setupMode ? "home" : "settings", null, false), 1500); } catch (e) { toast(e.message, true); } };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); connect(); } });
    p.append(n.security ? input : h("div", {}), h("div", { style: "margin-top:14px" }, h("button", { class: "f btn primary", onclick: connect }, "Se connecter"), h("button", { class: "f btn", onclick: closePanel }, "Annuler")));
    setFocus(n.security ? input : $(".btn", p));
  };

  // ------------------------------------------------------------------ player
  const video = $("#video"); const playerEl = $("#player"); const osd = $("#osd");
  let osdTimer, progressTimer;
  const showOsd = (ms = 4000) => { osd.classList.add("show"); clearTimeout(osdTimer); if (ms) osdTimer = setTimeout(() => osd.classList.remove("show"), ms); };
  const destroyEngine = () => { if (state.engine) { try { state.engine.destroy(); } catch (_) { /* */ } state.engine = null; } video.pause(); video.removeAttribute("src"); video.load(); };
  // Engines get absolute URLs: mpegts.js fetches from a Worker, where "/api/proxy?..." cannot be parsed.
  const absolute = (url) => new URL(url, location.href).href;
  const pickEngine = (url) => {
    const u = new URL(url, location.href);
    const proxiedTarget = u.origin === location.origin && u.pathname === "/api/proxy" ? u.searchParams.get("url") : null;
    let path = "";
    try { path = new URL(proxiedTarget || u.href).pathname.toLowerCase(); } catch (_) { path = ""; }
    const last = path.split("/").pop() || "";
    const ext = last.includes(".") ? last.split(".").pop() : "";
    if (ext === "m3u8" || ext === "m3u") return window.Hls && Hls.isSupported() ? "hls" : "native";
    if (["mp4", "m4v", "webm", "mov", "ogv"].includes(ext)) return "native";
    if (ext === "ts" || ext === "") return window.mpegts && mpegts.getFeatureList().mseLivePlayback ? "mpegts" : "native";
    return "native";
  };
  // Tuned for cheap hardware on a home connection: a deep enough buffer to ride out CDN hiccups,
  // short network timeouts so a dead segment never costs more than a second, and no JS stash
  // buffer on live MPEG-TS (it only adds latency).
  const HLS_CONFIG = {
    enableWorker: true,
    lowLatencyMode: false,
    backBufferLength: 30,
    maxBufferLength: 40,
    maxMaxBufferLength: 120,
    maxBufferSize: 100 * 1000 * 1000,
    liveSyncDurationCount: 3,
    liveMaxLatencyDurationCount: 12,
    manifestLoadingTimeOut: 6000,
    manifestLoadingMaxRetry: 2,
    manifestLoadingRetryDelay: 500,
    levelLoadingTimeOut: 6000,
    levelLoadingMaxRetry: 3,
    levelLoadingRetryDelay: 500,
    fragLoadingTimeOut: 14000,
    fragLoadingMaxRetry: 3,
    fragLoadingRetryDelay: 500,
    startFragPrefetch: true,
    abrEwmaFastLive: 2.0,
    abrEwmaSlowLive: 8.0,
    startLevel: -1,
  };
  const MPEGTS_CONFIG = {
    enableWorker: true,
    enableStashBuffer: false,
    stashInitialSize: 128,
    liveBufferLatencyChasing: true,
    liveBufferLatencyMaxLatency: 8,
    liveBufferLatencyMinRemain: 1.0,
    autoCleanupSourceBuffer: true,
    autoCleanupMaxBackwardDuration: 30,
    autoCleanupMinBackwardDuration: 15,
    lazyLoad: false,
  };

  const startPlayback = (item) => {
    destroyEngine();
    $("#spinner").hidden = false;
    const url = absolute(item.play_url || item.url);
    const hinted = (item.extra && item.extra.format) || "";
    const engine = hinted === "hls" ? (window.Hls && Hls.isSupported() ? "hls" : "native")
      : hinted === "mpegts" ? (window.mpegts && mpegts.getFeatureList().mseLivePlayback ? "mpegts" : "native")
      : hinted === "mp4" || hinted === "native" ? "native"
      : pickEngine(url);
    state.startedAt = Date.now();
    if (engine === "hls") {
      const hls = new Hls(HLS_CONFIG);
      hls.loadSource(url);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, () => video.play().catch(() => {}));
      hls.on(Hls.Events.ERROR, (_, data) => {
        if (!data.fatal) return;
        if (data.type === Hls.ErrorTypes.MEDIA_ERROR && state.retries < 2) { state.retries++; hls.recoverMediaError(); return; }
        if (data.type === Hls.ErrorTypes.NETWORK_ERROR && item.direct && !item._proxied) { fallbackToProxy(item); return; }
        if (data.type === Hls.ErrorTypes.NETWORK_ERROR && state.retries < 1) { state.retries++; setTimeout(() => hls.startLoad(), 400); return; }
        playbackFailed("Flux indisponible");
      });
      state.engine = hls;
    } else if (engine === "mpegts") {
      const p = mpegts.createPlayer({ type: "mpegts", isLive: item.kind === "live", url }, MPEGTS_CONFIG);
      p.attachMediaElement(video);
      p.on(mpegts.Events.ERROR, () => {
        if (item.direct && !item._proxied) { fallbackToProxy(item); return; }
        if (state.retries < 1) { state.retries++; setTimeout(() => { try { p.unload(); p.load(); p.play(); } catch (_) { playbackFailed("Flux indisponible"); } }, 400); }
        else playbackFailed("Flux indisponible");
      });
      p.load();
      p.play().catch(() => {});
      state.engine = p;
    } else {
      video.src = url;
      video.play().catch(() => {});
    }
  };

  // A CDN can allow CORS on the playlist but not on the segments. Before declaring the source dead,
  // replay the same URL through the local proxy.
  const fallbackToProxy = (item) => {
    const viaProxy = { ...item, direct: false, _proxied: true, play_url: "/api/proxy?url=" + encodeURIComponent(item.url) };
    state.playing = { ...state.playing, ...viaProxy };
    state.retries = 0;
    startPlayback(viaProxy);
  };

  const playbackFailed = async (why) => {
    const alt = state.alternatives.shift();
    if (alt) {
      state.sourceIndex = (state.sourceIndex || 1) + 1;
      toast(`${why} · source ${state.sourceIndex}…`);
      state.retries = 0;
      state.playing = { ...state.playing, ...alt };
      startPlayback(alt);
      return;
    }
    toast(why + " · aucune source ne répond", true);
    sendWs({ type: "error", error: why });
    stopPlayback();
  };

  const playItem = async (item, list = [], silent = false, position = 0) => {
    if (item.kind === "series" && item.url.startsWith("xtream-series://")) return openVod(item);
    try {
      const r = await post("/play", { channel_id: item.id && !item.id.includes("-s") ? item.id : "", url: item.id && !item.id.includes("-s") ? "" : item.url, name: item.name, kind: item.kind || "live", position });
      if (r.state.backend === "mpv") { showExternal(item); return; }
      state.zapList = list.filter((x) => x.kind === "live" || x.kind === "vod");
      state.zapIndex = state.zapList.findIndex((x) => x.id === item.id);
      state.retries = 0;
      state.sourceIndex = 1;
      beginBrowserPlayback({ ...item, ...r.item, id: item.id }, r.alternatives || [], position, r.token);
    } catch (e) { toast(e.message, true); }
  };
  const beginBrowserPlayback = (item, alternatives, position, token) => {
    if (token && token === state.lastToken) return; // already started from the other channel
    if (token) state.lastToken = token;
    state.playing = item; state.alternatives = (alternatives || []).slice();
    hideExternal(); playerEl.hidden = false; document.body.classList.add("playing");
    $("#osd-name").textContent = cleanTitle(item.name); $("#osd-logo").src = item.kind === "live" ? (item.logo || "") : ""; $("#osd-sub").textContent = item.group || "";
    $("#osd-badge").textContent = item.kind === "live" ? "Direct" : "Vidéo"; $("#osd-badge").className = "badge " + (item.kind === "live" ? "live" : "soon");
    $("#osd-now").textContent = ""; updateNow(item); startPlayback(item);
    if (position > 5) video.addEventListener("loadedmetadata", () => { video.currentTime = position; }, { once: true });
    showOsd(); clearInterval(progressTimer);
    progressTimer = setInterval(() => {
      if (playerEl.hidden) return;
      const dur = isFinite(video.duration) ? video.duration : 0;
      $("#osd-pos").textContent = fmtDur(video.currentTime); $("#osd-dur").textContent = dur ? fmtDur(dur) : "";
      if (dur) $("#osd-progress").style.width = (video.currentTime / dur) * 100 + "%"; else if (state.playing && state.playing.now) $("#osd-progress").style.width = progressPct(state.playing.now) + "%";
      sendWs({ type: "progress", position: video.currentTime, duration: dur, paused: video.paused });
    }, 3000);
  };
  const updateNow = async (item) => {
    if (item.kind !== "live" || !item.id) return;
    try { const d = await api("/channels/" + item.id); state.playing = { ...state.playing, now: d.now, next: d.next }; $("#osd-now").replaceChildren(d.now ? d.now.title : "", d.next ? h("small", {}, "Ensuite : " + d.next.title + " à " + fmtTime(d.next.start)) : ""); if (d.alternatives && !state.alternatives.length) state.alternatives = d.alternatives; } catch (_) { /* */ }
  };
  const stopPlayback = (notify = true) => {
    const wasVod = state.playing && state.playing.kind !== "live";
    const wasExternal = !$("#external").hidden;
    destroyEngine(); clearInterval(progressTimer); playerEl.hidden = true; hideExternal(); state.playing = null;
    if (notify) post("/stop").catch(() => {});
    setFocus(focused && focused.isConnected ? focused : $(".f", main), false);
    // Resume bars changed: redraw the screen the viewer came back to.
    if ((wasVod || wasExternal) && ["movies", "series", "library", "home"].includes(state.view) && !$(".panel")) go(state.view, state.viewArg, false);
  };
  const hideExternal = () => { $("#external").hidden = true; document.body.classList.remove("playing"); };
  const showExternal = (item) => {
    destroyEngine(); playerEl.hidden = true;
    $("#ext-name").textContent = item.name || "";
    const poster = $("#ext-poster");
    poster.hidden = !item.logo;
    if (item.logo) poster.src = item.logo;
    $("#external").hidden = false; document.body.classList.add("playing");
  };
  const zap = (delta) => { if (!state.zapList.length) return; const idx = (state.zapIndex + delta + state.zapList.length) % state.zapList.length; state.zapIndex = idx; playItem(state.zapList[idx], state.zapList); };
  video.addEventListener("playing", () => {
    $("#spinner").hidden = true;
    state.retries = 0;
    if (state.startedAt) { console.info(`[aura] first frame in ${Date.now() - state.startedAt} ms`); state.startedAt = 0; }
  });
  video.addEventListener("waiting", () => { $("#spinner").hidden = false; });
  video.addEventListener("ended", () => {
    const finished = state.playing;
    sendWs({ type: "progress", position: video.duration, duration: video.duration, paused: true });
    stopPlayback();
    if (finished && finished.extra && finished.extra.library) library.ended(finished);
  });
  video.addEventListener("error", () => {
    if (state.engine) return;
    const cur = state.playing;
    if (cur && cur.direct && !cur._proxied) { fallbackToProxy(cur); return; }
    playbackFailed("Format non lisible par le navigateur");
  });
  const playerKey = (key) => {
    if (!$("#external").hidden) {
      // mpv owns the screen: a keyboard on the TV page drives it through the API.
      const route = { Escape: "/stop", Backspace: "/stop", BrowserBack: "/stop", Enter: "/player/pause", Space: "/player/pause", MediaPlayPause: "/player/pause", ArrowRight: "/player/seek/30", ArrowLeft: "/player/seek/-15", a: "/player/cycle/audio", s: "/player/cycle/sub" }[key];
      if (route) post(route).catch(() => {});
      if (route === "/stop") hideExternal();
      return true;
    }
    if (playerEl.hidden) return false;
    const isVod = state.playing && state.playing.kind !== "live";
    switch (key) {
      case "Escape": case "Backspace": case "BrowserBack": stopPlayback(); break;
      case "Enter": case " ": case "Space": case "MediaPlayPause": if (osd.classList.contains("show") || key !== "Enter") { video.paused ? video.play() : video.pause(); } showOsd(); break;
      case "ArrowUp": if (isVod) showOsd(); else zap(-1); break;
      case "ArrowDown": if (isVod) showOsd(); else zap(1); break;
      case "ArrowRight": if (isVod) video.currentTime += 30; showOsd(); break;
      case "ArrowLeft": if (isVod) video.currentTime -= 15; showOsd(); break;
      case "i": case "Info": showOsd(6000); break;
      case "f": case "F": if (state.playing && state.playing.id && !String(state.playing.id).startsWith("lib:")) toggleFav(state.playing); break;
      default: showOsd();
    }
    return true;
  };

  // ------------------------------------------------------------------ keyboard / mouse
  const handleKey = (key, text) => {
    if (playerKey(key)) return;
    const active = document.activeElement; const inInput = active && active.tagName === "INPUT";
    if (inInput && !["ArrowUp", "ArrowDown", "Escape", "Backspace", "BrowserBack", "Enter", "Tab"].includes(key)) {
      if (key === "ArrowLeft" || key === "ArrowRight") return;
      if (key.length === 1 && text === undefined) return;
      if (text) { active.value += text; active.dispatchEvent(new Event("input")); }
      return;
    }
    if (inInput && key === "Backspace") { if (active.value) { active.value = active.value.slice(0, -1); active.dispatchEvent(new Event("input")); } else back(); return; }
    switch (key) {
      case "ArrowUp": case "ArrowDown": case "ArrowLeft": case "ArrowRight": moveFocus(key); break;
      case "Enter": if (focused) { const el = focused; el.classList.add("pressed"); setTimeout(() => el.classList.remove("pressed"), 130); el.click(); } break;
      case "Escape": case "Backspace": case "BrowserBack": back(); break;
      case "Home": go("home"); break;
      default: break;
    }
  };
  document.addEventListener("keydown", (e) => {
    const nav = ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Enter", "Escape", "Backspace", "BrowserBack", "Home", " ", "MediaPlayPause"];
    const active = document.activeElement; const inInput = active && active.tagName === "INPUT";
    if (inInput && (e.key === "ArrowLeft" || e.key === "ArrowRight")) return;
    if (inInput && e.key === "Backspace" && active.value) return;
    if (inInput && e.key === "Enter") return; // inputs handle Enter themselves
    if (nav.includes(e.key) || (!inInput && e.key.length === 1)) { e.preventDefault(); handleKey(e.key === " " ? "Space" : e.key); }
  });
  document.addEventListener("mousemove", () => { document.body.classList.add("mouse"); clearTimeout(document.body._mt); document.body._mt = setTimeout(() => document.body.classList.remove("mouse"), 3000); });
  document.addEventListener("click", (e) => { const f = e.target.closest(".f"); if (f) setFocus(f, false); if (!playerEl.hidden && e.target.closest("#player")) showOsd(); });
  document.addEventListener("wheel", (e) => { const cont = e.target.closest(".list, .scroller, .grid, .panel-inner, .episodes"); if (cont) cont.scrollTop += e.deltaY; }, { passive: true });

  // ------------------------------------------------------------------ websocket
  const sendWs = (obj) => { if (state.ws && state.ws.readyState === 1) state.ws.send(JSON.stringify(obj)); };
  const connectWs = () => {
    const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/api/ws?role=tv"); state.ws = ws;
    ws.onmessage = (ev) => {
      let m; try { m = JSON.parse(ev.data); } catch (_) { return; }
      switch (m.type) {
        case "play": beginBrowserPlayback(m.item, m.alternatives, m.position || 0, m.token); break;
        case "external_player": showExternal(m.item); break;
        case "stop": stopPlayback(false); break;
        case "key": handleKey(m.key, m.text); break;
        case "text": { const a = document.activeElement; if (a && a.tagName === "INPUT") { a.value = m.text; a.dispatchEvent(new Event("input")); } else go("search", { q: m.text }); break; }
        case "navigate": go(m.view === "sports" ? "sports" : m.view); break;
        case "pause_toggle": if (!playerEl.hidden) { video.paused ? video.play() : video.pause(); showOsd(); } break;
        case "seek": if (!playerEl.hidden) { video.currentTime += m.seconds; showOsd(); } break;
        case "volume": showVolume(m); break;
        case "catalog_changed": case "epg_changed": if (state.view === "home" || state.setupMode) go("home", null, false); else if (["movies", "series"].includes(state.view) && !$(".panel")) go(state.view, state.viewArg, false); break;
        case "network_changed": updateNet(); if (state.setupMode) go("home", null, false); break;
        case "system": toast(m.action === "reboot" ? "Redémarrage…" : "Extinction…"); break;
        case "settings_changed": applyAccent(); break;
        default: library.onEvent(m); break;
      }
    };
    ws.onclose = () => setTimeout(connectWs, 2000); ws.onerror = () => ws.close();
  };
  let vol = 60;
  const showVolume = (m) => { if (m.mute) { toast("Muet"); return; } if (m.percent !== null && m.percent !== undefined) vol = m.percent; else vol = Math.max(0, Math.min(100, vol + (m.delta || 0))); $("#vfill").style.width = vol + "%"; $("#vtext").textContent = vol + "%"; const v = $("#volume-osd"); v.hidden = false; clearTimeout(v._t); v._t = setTimeout(() => (v.hidden = true), 1800); };

  // ------------------------------------------------------------------ boot
  const updateNet = async () => { try { const n = await api("/network"); $("#netdot").classList.toggle("on", n.online); } catch (_) { $("#netdot").classList.remove("on"); } };
  setInterval(() => { $("#clock").textContent = new Date().toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" }); }, 1000);
  setInterval(updateNet, 30000);
  setInterval(() => { if (state.setupMode && state.view === "home") go("home", null, false); }, 10000);
  setInterval(() => { if (state.playing && state.playing.kind === "live") updateNow(state.playing); }, 60000);
  // Nothing to theme any more: the palette is black and white everywhere.
  const applyAccent = () => {};
  window.AuraIcons.hydrate(); applyAccent(); updateNet(); connectWs(); go("home", null, false);
})();
