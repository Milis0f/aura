/* Aura — TV: the local library (films and series found on the drives, films added by link).
   Loaded before tv.js, which calls this factory with the helpers it needs (DOM, API, focus, panels, player). */
window.AuraTVLibrary = (tv) => {
  "use strict";
  const { $, $$, h, I, api, post, toast, emptyState, stagger, fmtDur, fmtMin, rating, state } = tv;
  const code = (s, e) => `S${String(s).padStart(2, "0")}E${String(e).padStart(2, "0")}`;
  const pct = (p) => Math.round(Math.min(1, Math.max(0, p || 0)) * 100);
  const cssUrl = (url) => String(url).replace(/["\\\n]/g, (c) => `\\${c}`);
  const plural = (n, word) => `${n} ${word}${n > 1 ? "s" : ""}`;
  const hostOf = (url) => { try { return new URL(url).hostname.replace(/^www\./, ""); } catch (_) { return "lien"; } };

  const placeholder = (it, cls = "lib-ph") => h("div", { class: cls },
    h("span", { class: "lib-ph-title" }, it.title), it.year ? h("span", { class: "lib-ph-year num" }, it.year) : null);

  const thumb = (it) => {
    const box = h("div", { class: "thumb" });
    if (it.poster) {
      const img = h("img", { src: it.poster, alt: "", loading: "lazy" });
      img.onerror = () => img.replaceWith(placeholder(it));
      box.append(img);
    } else box.append(placeholder(it));
    return box;
  };

  const subline = (it) => {
    if (!it.online) return "Disque débranché";
    if (it.kind === "series") return [it.year, it.seasons ? plural(it.seasons, "saison") : ""].filter(Boolean).join(" · ") || "Série";
    if (it.kind === "link") return [it.year, hostOf(it.url)].filter(Boolean).join(" · ");
    const minutes = it.runtime || Math.round((it.duration || 0) / 60);
    return [it.year, minutes ? fmtMin(minutes) : ""].filter(Boolean).join(" · ") || "Film";
  };

  const meta = (it) => [
    it.year ? h("span", { class: "badge pill num" }, it.year) : null,
    it.kind === "series" && it.seasons ? h("span", { class: "badge pill" }, plural(it.seasons, "saison")) : null,
    it.kind !== "series" && (it.runtime || it.duration) ? h("span", { class: "badge pill" }, fmtMin(it.runtime || Math.round(it.duration / 60))) : null,
    it.rating ? h("span", { class: "badge pill" }, rating(it.rating)) : null,
    it.quality ? h("span", { class: "badge pill" }, it.quality) : null,
    ...(it.langs || []).map((l) => h("span", { class: "badge pill" }, l)),
    ...(it.genres || []).slice(0, 2).map((g) => h("span", { class: "badge pill" }, g)),
  ].filter(Boolean);

  const card = (it) => h("button", { class: `f card poster lib${it.online ? "" : " offline"}`, dataset: { label: it.title }, onclick: () => open(it) },
    thumb(it),
    h("div", { class: "lib-badges" },
      it.quality ? h("span", { class: `badge${it.quality === "4K" ? " soon" : ""}` }, it.quality) : null,
      (it.langs || []).slice(0, 2).map((l) => h("span", { class: "badge" }, l))),
    it.resume && !it.resume.finished && it.resume.progress > 0.01 ? h("div", { class: "resume" }, h("i", { style: `width:${pct(it.resume.progress)}%` })) : null,
    h("div", { class: "body" }, h("div", { class: "title" }, it.title), h("div", { class: "sub" }, subline(it))));

  const row = (items) => h("div", { class: "row" }, items.map((it, i) => stagger(card(it), i)));

  const scan = async () => {
    try { const r = await post("/library/scan"); toast(r.scheduled ? "Analyse des disques lancée" : "Une analyse est déjà en cours"); } catch (e) { toast(e.message, true); }
  };

  /* ------------------------------------------------------------ home rows */
  const homeRows = (lib) => {
    const out = [];
    const total = lib.counts ? lib.counts.films + lib.counts.series + lib.counts.links : 0;
    if (lib.resume && lib.resume.length) out.push(h("h2", {}, "Reprendre"), row(lib.resume));
    if (lib.recent && lib.recent.length) out.push(h("h2", {}, "Sur tes disques", h("span", { class: "count num" }, String(total))), row(lib.recent));
    return out;
  };

  /* ------------------------------------------------------------ library screen */
  const banner = (it) => {
    const resume = it.resume && !it.resume.finished && it.resume.position > 30;
    const art = it.backdrop || it.poster;
    return h("button", { class: "f banner", dataset: { label: it.title }, onclick: () => open(it) },
      h("div", { class: "bg", style: art ? `background-image:url("${cssUrl(art)}")` : "" }), h("div", { class: "veil" }),
      h("div", { class: "txt" },
        h("div", { class: "k" }, resume ? "Reprendre" : "Sur tes disques"),
        h("div", { class: "t" }, it.title),
        h("div", { class: "meta" }, meta(it)),
        h("div", { class: "d" }, it.overview || "")),
      h("div", { class: "art" }, it.poster ? h("img", { src: it.poster, alt: "" }) : null));
  };

  const view = async (arg) => {
    const kind = (arg && arg.kind) || "all";
    const drive = (arg && arg.drive) || "";
    const query = new URLSearchParams({ limit: "150" });
    if (kind !== "all") query.set("kind", kind);
    if (drive) query.set("drive", drive);
    const [home, list] = await Promise.all([api("/library/home"), api(`/library/items?${query}`)]);
    const v = h("div", { class: "view" });
    const sc = h("div", { class: "scroller", style: "height:100%" });
    v.append(sc);
    if (!home.total) {
      const plugged = home.drives.some((d) => d.available);
      sc.append(h("h1", {}, "Bibliothèque"), emptyState("drive",
        plugged ? "Aucun film trouvé pour l'instant" : "Branche un disque dur",
        plugged ? "Les disques branchés sont en cours d'analyse : les films et les séries apparaissent ici tout seuls."
          : "Aura le détecte tout seul, cherche les films et les séries, et les range ici avec l'affiche et le résumé.",
        h("button", { class: "f btn primary", onclick: scan }, I("refresh"), "Analyser les disques")));
      tv.setAmbient("");
      tv.render(v);
      return;
    }
    const hero = home.hero;
    if (hero && kind === "all" && !drive) sc.append(banner(hero)); else sc.append(h("h1", {}, "Bibliothèque"));
    const plugged = home.drives.filter((d) => d.available);
    sc.append(h("div", { class: "chips" },
      [["all", "Tout"], ["film", "Films"], ["series", "Séries"], ["link", "Liens"]].map(([k, label]) =>
        h("button", { class: `f chip${k === kind ? " active" : ""}`, onclick: () => tv.go("library", { kind: k, drive }, false) }, label)),
      plugged.length > 1 ? plugged.map((d) => h("button", { class: `f chip${d.id === drive ? " active" : ""}`, onclick: () => tv.go("library", { kind, drive: d.id === drive ? "" : d.id }, false) }, I("drive"), d.label)) : null,
      h("button", { class: "f chip", onclick: scan }, I("refresh"), "Analyser")));
    const resume = home.rows.find((r) => r.key === "continue");
    if (resume && kind === "all" && !drive) sc.append(h("h2", {}, "Reprendre"), row(resume.items));
    const title = { all: "Tous les titres", film: "Films", series: "Séries", link: "Mes liens" }[kind];
    sc.append(h("h2", {}, title, h("span", { class: "count num" }, String(list.total))));
    if (!list.items.length) sc.append(emptyState("film", "Rien ici", "Change de filtre, ou branche le disque qui contient ces titres."));
    else sc.append(h("div", { class: "lib-grid" }, list.items.map((it, i) => stagger(card(it), i))));
    tv.setAmbient(hero ? hero.backdrop || hero.poster : "");
    tv.render(v, ".banner, .chip");
  };

  /* ------------------------------------------------------------ details and playback */
  const start = async (item, fileId, position) => {
    try {
      const r = await post("/library/play", { item_id: item.id, file_id: fileId || "", position: position || 0 });
      const backend = r.backend || (r.state && r.state.backend);
      tv.closePanel();
      if (backend === "mpv") tv.external({ name: item.title, logo: item.poster });
      else if (r.item) tv.play({ ...r.item, logo: item.poster || r.item.logo }, [], r.position || position || 0, r.token);
    } catch (e) { toast(e.message, true); }
  };

  const seasons = (d, extra) => {
    const tabs = h("div", { class: "seasons" });
    const list = h("div", { class: "episodes list" });
    const show = (season) => {
      $$(".tab", tabs).forEach((t) => t.classList.toggle("active", Number(t.dataset.s) === season.season));
      list.replaceChildren(...season.episodes.map((ep) => h("button", {
        class: `f item${ep.online ? "" : " offline"}`, dataset: { label: code(ep.season, ep.episode) },
        onclick: () => (ep.online ? start(d, ep.id, ep.finished ? 0 : ep.position) : toast("Le disque de cet épisode est débranché", true)),
      },
        h("div", { class: "ep-num num" }, ep.finished ? I("check") : String(ep.episode)),
        h("div", { class: "grow" },
          h("div", { class: "name" }, ep.title || `Épisode ${ep.episode}`),
          h("div", { class: "sub" }, [code(ep.season, ep.episode), ep.duration ? fmtMin(Math.round(ep.duration / 60)) : "", ep.quality, ep.online ? "" : "débranché"].filter(Boolean).join(" · "))),
        ep.duration && ep.position > 30 && !ep.finished ? h("div", { class: "bar" }, h("div", { class: "bar-fill", style: `width:${pct(ep.position / ep.duration)}%` })) : null)));
    };
    d.seasons_list.forEach((s) => tabs.append(h("button", { class: "f tab", "data-s": s.season, onclick: () => { show(s); tv.setFocus($(".f", list)); } }, s.season ? `Saison ${s.season}` : "Bonus")));
    extra.append(tabs, list);
    show(d.seasons_list.find((s) => d.play && s.season === d.play.season) || d.seasons_list[0]);
  };

  const versions = (d, extra) => {
    const list = h("div", { class: "list", style: "height:auto;margin-top:18px" }, h("h2", {}, `${d.versions.length} versions`));
    d.versions.forEach((ver) => list.append(h("button", {
      class: `f item${ver.online ? "" : " offline"}`, dataset: { label: ver.name },
      onclick: () => (ver.online ? start(d, ver.id, ver.finished ? 0 : ver.position) : toast("Disque débranché", true)),
    },
      h("div", { class: "grow" },
        h("div", { class: "name" }, [ver.quality || "Version", ...ver.langs].join(" · ")),
        h("div", { class: "sub" }, `${ver.drive} · ${ver.name}`)))));
    extra.append(list);
  };

  const open = async (it) => {
    const p = tv.panel(it.title, "", it.backdrop || it.poster);
    const info = h("div", {});
    const actions = h("div", { class: "actions" });
    const poster = it.poster ? h("img", { class: "poster-big", src: it.poster, alt: "" }) : placeholder(it, "poster-big lib-ph");
    const extra = h("div", {});
    p.append(h("div", { class: "detail" }, h("div", {}, poster), info), extra);
    let d;
    try { d = await api(`/library/items/${encodeURIComponent(it.id)}`); } catch (e) { toast(e.message, true); return; }
    // Element.append writes null as the text "null": keep only real nodes.
    info.append(...[
      h("div", { class: "meta" }, meta(d)),
      d.overview ? h("p", {}, d.overview) : null,
      d.online ? null : h("div", { class: "lib-callout" }, I("drive"), `Branche le disque « ${d.drives.join(" » ou « ")} » pour lire ce titre.`),
      actions,
    ].filter(Boolean));
    const play = d.play;
    if (play) {
      const label = play.resume ? `Reprendre à ${fmtDur(play.position)}` : play.episode ? `Lire ${code(play.season, play.episode)}` : "Lire";
      actions.append(h("button", { class: "f btn primary", onclick: () => start(d, play.file_id, play.position) }, I("play"), label));
      if (play.resume) actions.append(h("button", { class: "f btn", onclick: () => start(d, play.file_id, 0) }, I("restart"), "Depuis le début"));
    }
    if (d.kind === "series" && d.seasons_list && d.seasons_list.length) seasons(d, extra);
    if (d.kind === "film" && d.versions && d.versions.length > 1) versions(d, extra);
    tv.setFocus($(".f", actions) || $(".f", extra) || $(".close", p.parentElement));
  };

  /* ------------------------------------------------------------ end of an episode, live events */
  const ended = async (item) => {
    const fileId = item.extra && item.extra.file_id;
    if (!fileId) return;
    try {
      const r = await post("/library/ended", { file_id: fileId });
      if (!r.next || !r.autoplay) return;
      toast(`Épisode suivant : ${code(r.next.season, r.next.episode)}${r.next.title ? ` · ${r.next.title}` : ""}`);
      setTimeout(async () => {
        try {
          const next = await post("/library/play", { file_id: r.next.file_id, position: 0 });
          const backend = next.backend || (next.state && next.state.backend);
          if (backend === "browser" && next.item) tv.play({ ...next.item, logo: item.logo }, [], 0, next.token);
        } catch (e) { toast(e.message, true); }
      }, 2500);
    } catch (_) { /* nothing to chain */ }
  };

  let refreshTimer;
  const onEvent = (m) => {
    const label = m.drive ? m.drive.label : "";
    if (m.type === "drive_added") toast(`Disque « ${label} » branché · recherche des films…`);
    else if (m.type === "drive_removed") toast(`« ${label} » débranché`);
    else if (m.type === "library_scan" && m.state === "done") {
      if (m.new_items) toast(`${m.new_items} ${m.new_items > 1 ? "nouveaux titres" : "nouveau titre"} sur « ${label} »`);
      else if (m.films !== undefined) toast(`« ${label} » : ${plural(m.films, "film")}, ${plural(m.episodes, "épisode")}`);
    } else if (m.type !== "library_changed") return;
    if (!["library", "home"].includes(state.view) || $(".panel") || !$("#player").hidden || !$("#external").hidden) return;
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => tv.go(state.view, state.viewArg, false), 1200);
  };

  return { view, open, homeRows, ended, onEvent };
};
