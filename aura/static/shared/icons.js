/* Aura — hand-drawn 24px stroke icons shared by the TV page and the phone remote. No emoji, no font glyphs. */
(() => {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const circle = (cx, cy, r) => `M${cx - r} ${cy}a${r} ${r} 0 1 0 ${2 * r} 0a${r} ${r} 0 1 0 ${-2 * r} 0`;
  const speaker = "M4 9.5h3.5L12 5.5v13l-4.5-4H4z";

  const PATHS = {
    play: ["M8 5.5v13l10.5-6.5z"],
    pause: ["M7.5 5h3v14h-3z", "M13.5 5h3v14h-3z"],
    stop: ["M6.5 6.5h11v11h-11z"],
    "star-fill": ["M12 3.8l2.5 5.1 5.6.8-4.05 3.95.96 5.57L12 16.6l-5.01 2.62.96-5.57L3.9 9.7l5.6-.8z"],
    star: ["M12 3.8l2.5 5.1 5.6.8-4.05 3.95.96 5.57L12 16.6l-5.01 2.62.96-5.57L3.9 9.7l5.6-.8z"],
    back: ["M9 14l-5-5 5-5", "M4 9h10.5a5.5 5.5 0 0 1 0 11H11"],
    home: ["M3.5 10.5L12 4l8.5 6.5", "M5.5 9.5V20h13V9.5", "M10 20v-5.5h4V20"],
    "chevron-right": ["M9 5l7 7-7 7"],
    "chevron-left": ["M15 5l-7 7 7 7"],
    "chevron-up": ["M5 15l7-7 7 7"],
    "chevron-down": ["M5 9l7 7 7-7"],
    "arrow-right": ["M5 12h14", "M13 6l6 6-6 6"],
    "arrow-left": ["M19 12H5", "M11 6l-6 6 6 6"],
    close: ["M6 6l12 12", "M18 6L6 18"],
    check: ["M4.5 12.5l4.5 4.5L19.5 7"],
    plus: ["M12 5v14", "M5 12h14"],
    search: [circle(11, 11, 6.5), "M16 16l4.5 4.5"],
    "vol-down": [speaker, "M15.5 9.5a3.5 3.5 0 0 1 0 5"],
    "vol-up": [speaker, "M15.5 9.5a3.5 3.5 0 0 1 0 5", "M18 7a7 7 0 0 1 0 10"],
    mute: [speaker, "M16 9.5l5 5", "M21 9.5l-5 5"],
    info: [circle(12, 12, 9), "M12 11v5.5", "M12 7.8v.2"],
    trash: ["M4.5 7h15", "M9.5 7V4.5h5V7", "M6.5 7l1 12.5h9l1-12.5", "M10.5 11v5", "M13.5 11v5"],
    refresh: ["M19.5 12a7.5 7.5 0 1 1-2.2-5.3", "M19.5 4.5v4h-4"],
    restart: ["M4.5 12a7.5 7.5 0 1 0 2.2-5.3", "M4.5 4.5v4h4"],
    power: ["M12 3.5v8", "M7 6.2a7.5 7.5 0 1 0 10 0"],
    update: ["M12 15.5V4.5", "M7.5 9L12 4.5 16.5 9", "M4.5 15.5v4h15v-4"],
    monitor: ["M3.5 5h17v11h-17z", "M9 20h6", "M12 16v4"],
    film: ["M4 4.5h16v15H4z", "M8 4.5v15", "M16 4.5v15", "M4 9h4", "M4 15h4", "M16 9h4", "M16 15h4"],
    tv: ["M3.5 7h17v12h-17z", "M8.5 3l3.5 4 3.5-4"],
    stack: ["M12 4l8.5 4.5L12 13 3.5 8.5z", "M3.5 12.5L12 17l8.5-4.5", "M3.5 16.5L12 21l8.5-4.5"],
    grid: ["M4.5 4.5h6v6h-6z", "M13.5 4.5h6v6h-6z", "M4.5 13.5h6v6h-6z", "M13.5 13.5h6v6h-6z"],
    sliders: ["M4 6.5h9", "M17 6.5h3", "M4 12h3", "M11 12h9", "M4 17.5h7", "M15 17.5h5", circle(15, 6.5, 2), circle(9, 12, 2), circle(13, 17.5, 2)],
    remote: ["M8 3h8a1.5 1.5 0 0 1 1.5 1.5v15A1.5 1.5 0 0 1 16 21H8a1.5 1.5 0 0 1-1.5-1.5v-15A1.5 1.5 0 0 1 8 3z", circle(12, 8.5, 2), "M10 14h.01", "M14 14h.01", "M10 17h.01", "M14 17h.01"],
    trophy: ["M8 4.5h8V9a4 4 0 0 1-8 0z", "M8 6H5.5A2.5 2.5 0 0 0 8 9.5", "M16 6h2.5A2.5 2.5 0 0 1 16 9.5", "M12 13v3.5", "M8.5 20h7", "M9.5 20l.5-3.5h4l.5 3.5"],
    flag: ["M5.5 21V4", "M5.5 4.5h12l-2.5 4 2.5 4h-12"],
    octagon: ["M8.3 3.5h7.4l5.3 5.3v6.4l-5.3 5.3H8.3L3 15.2V8.8z"],
    glove: ["M7 11V8a5 5 0 0 1 10 0v5.5a5 5 0 0 1-5 5H9.5A2.5 2.5 0 0 1 7 16z", "M7 12.5h5"],
    ball: [circle(12, 12, 9), "M12 7.5l3.4 2.5-1.3 4h-4.2l-1.3-4z", "M12 7.5V3.5", "M15.4 10l3.8-1.3", "M14.1 14l2.4 3.4", "M9.9 14l-2.4 3.4", "M8.6 10L4.8 8.7"],
    basket: [circle(12, 12, 9), "M3 12h18", "M12 3v18", "M5.6 5.6a12 12 0 0 1 0 12.8", "M18.4 5.6a12 12 0 0 0 0 12.8"],
    wifi: ["M4 9.5a11.5 11.5 0 0 1 16 0", "M7 12.8a7 7 0 0 1 10 0", "M10 16a2.8 2.8 0 0 1 4 0", "M12 19.2v.1"],
    keyboard: ["M3.5 6.5h17v11h-17z", "M7 10h.01", "M10 10h.01", "M13 10h.01", "M16 10h.01", "M7 13.5h10"],
    cursor: ["M6 4l12.5 7-5.5 1.5-2.5 5.5z"],
    dpad: ["M9.5 3.5h5v6h6v5h-6v6h-5v-6h-6v-5h6z"],
    backspace: ["M9 5.5h11.5v13H9L3.5 12z", "M12.5 9.5l5 5", "M17.5 9.5l-5 5"],
    enter: ["M19.5 5.5V12a3 3 0 0 1-3 3H5", "M9 11l-4 4 4 4"],
    space: ["M4.5 12v3.5h15V12"],
    calendar: ["M4.5 6h15v14h-15z", "M4.5 10h15", "M8.5 3.5v4", "M15.5 3.5v4"],
    link: ["M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1", "M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"],
    signal: ["M5 19.5v-3", "M9.5 19.5v-6", "M14 19.5v-9", "M18.5 19.5V4.5"],
    guide: ["M4 5.5h16", "M4 10h16", "M4 14.5h10", "M4 19h7"],
    "play-circle": [circle(12, 12, 9), "M10 8.5v7l6-3.5z"],
    compass: [circle(12, 12, 9), "M15.5 8.5l-2 5-5 2 2-5z"],
    clapper: ["M4 9.5h16V20H4z", "M4 9.5l1.2-4.8 15.5 3.8", "M9 5.6l2 3.9", "M14.5 6.9l2 3.9"],
    folder: ["M3.5 7.5a2 2 0 0 1 2-2h3.8l2 2h7.2a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"],
    "folder-plus": ["M3.5 7.5a2 2 0 0 1 2-2h3.8l2 2h7.2a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z", "M12 10.5v5", "M9.5 13h5"],
    file: ["M14 3.5H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8.5z", "M14 3.5v5h5"],
    doc: ["M14 3.5H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8.5z", "M14 3.5v5h5", "M8.5 13h7", "M8.5 16.5h5"],
    image: ["M4 5.5h16v13H4z", circle(9, 10, 1.6), "M20 15.5l-4.5-4.5L6 18.5"],
    music: ["M9 17.5V5.5l10-2v12", circle(6.5, 17.5, 2.5), circle(16.5, 15.5, 2.5)],
    archive: ["M4 4.5h16V9H4z", "M5.5 9v10.5h13V9", "M10.5 12.5h3"],
    code: ["M9 8l-5 4 5 4", "M15 8l5 4-5 4"],
    download: ["M12 4v11", "M7.5 10.5L12 15l4.5-4.5", "M5 19.5h14"],
    upload: ["M12 15V4", "M7.5 8.5L12 4l4.5 4.5", "M5 19.5h14"],
    drive: ["M3.5 13.5l2.4-7.2A2 2 0 0 1 7.8 5h8.4a2 2 0 0 1 1.9 1.3l2.4 7.2", "M3.5 13.5h17v4a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z", "M16.5 16.5h.01", "M13.5 16.5h.01"],
    usb: ["M9 3.5h6v5H9z", "M7.5 8.5h9v7a4.5 4.5 0 0 1-9 0z", "M10.8 5.5v1", "M13.2 5.5v1"],
    eject: ["M5.5 14.5L12 6l6.5 8.5z", "M5.5 18.5h13"],
    lock: ["M6 10.5h12V20H6z", "M8.5 10.5v-3a3.5 3.5 0 0 1 7 0v3"],
    shield: ["M12 3.5l7 3V12c0 4.2-3 7.4-7 8.5-4-1.1-7-4.3-7-8.5V6.5z", "M9 12l2 2 4-4"],
    key: [circle(8, 14.5, 3.5), "M10.5 12l8.5-8.5", "M16 6.5l2.5 2.5", "M13.8 8.7l2 2"],
    user: [circle(12, 8.5, 3.5), "M5 20a7 7 0 0 1 14 0"],
    devices: ["M3.5 5.5h13v9h-13z", "M7 18.5h6", "M18.5 9.5h2.5v10h-2.5z"],
    activity: ["M3.5 12h4L10 6l4 12 2.5-6h4"],
    eye: ["M2.5 12s3.5-6.5 9.5-6.5 9.5 6.5 9.5 6.5-3.5 6.5-9.5 6.5S2.5 12 2.5 12z", circle(12, 12, 3)],
    menu: ["M4 7h16", "M4 12h16", "M4 17h16"],
    sort: ["M4 7h11", "M4 12h8", "M4 17h5", "M17.5 20V9", "M14.5 12l3-3 3 3"],
    list: ["M8.5 6.5h11", "M8.5 12h11", "M8.5 17.5h11", "M4.5 6.5h.01", "M4.5 12h.01", "M4.5 17.5h.01"],
    logout: ["M14.5 4.5h4v15h-4", "M10 8l-4 4 4 4", "M6 12h9"],
    magnet: ["M6 4.5v7a6 6 0 0 0 12 0v-7", "M6 4.5h3.5v7a2.5 2.5 0 0 0 5 0v-7H18", "M6 8.5h3.5", "M14.5 8.5H18"],
    gauge: ["M4.5 16a7.5 7.5 0 1 1 15 0", "M12 16l3.5-4.5", "M4.5 19.5h15"],
    clock: [circle(12, 12, 8.5), "M12 7.5V12l3 2"],
    subtitles: ["M3.5 5.5h17v13h-17z", "M7 14.5h4", "M13 14.5h4", "M7 11h7"],
    audio: ["M4 14v-4", "M8 17V7", "M12 20V4", "M16 16V8", "M20 13v-2"],
    edit: ["M4.5 19.5h4l10-10a2.8 2.8 0 0 0-4-4l-10 10z", "M13.5 6.5l4 4"],
    move: ["M3.5 7.5a2 2 0 0 1 2-2h3.8l2 2h7.2a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z", "M9.5 13h5.5", "M12.8 10.5l2.5 2.5-2.5 2.5"],
    cpu: ["M7 7h10v10H7z", "M10 10h4v4h-4z", "M9.5 3.5V7", "M14.5 3.5V7", "M9.5 17v3.5", "M14.5 17v3.5", "M3.5 9.5H7", "M3.5 14.5H7", "M17 9.5h3.5", "M17 14.5h3.5"],
    scan: ["M4 8V5.5A1.5 1.5 0 0 1 5.5 4H8", "M16 4h2.5A1.5 1.5 0 0 1 20 5.5V8", "M20 16v2.5a1.5 1.5 0 0 1-1.5 1.5H16", "M8 20H5.5A1.5 1.5 0 0 1 4 18.5V16", "M4 12h16"],
  };
  const SOLID = new Set(["play", "pause", "stop", "star-fill"]);

  const svg = (name, extraClass = "") => {
    const el = document.createElementNS(NS, "svg");
    el.setAttribute("viewBox", "0 0 24 24");
    el.setAttribute("aria-hidden", "true");
    el.setAttribute("class", ("i " + extraClass).trim());
    const solid = SOLID.has(name);
    el.setAttribute("fill", solid ? "currentColor" : "none");
    el.setAttribute("stroke", "currentColor");
    el.setAttribute("stroke-width", solid ? "1.2" : "1.6");
    el.setAttribute("stroke-linecap", "round");
    el.setAttribute("stroke-linejoin", "round");
    for (const d of PATHS[name] || PATHS.info) {
      const p = document.createElementNS(NS, "path");
      p.setAttribute("d", d);
      el.append(p);
    }
    return el;
  };

  const hydrate = (root = document) => {
    root.querySelectorAll("[data-icon]").forEach((el) => {
      if (el.dataset.iconReady === "1") return;
      el.prepend(svg(el.dataset.icon));
      el.dataset.iconReady = "1";
    });
  };

  window.AuraIcons = { svg, hydrate, names: Object.keys(PATHS) };
})();
