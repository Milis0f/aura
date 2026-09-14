/* Aura web — the viewer: file previews (image, audio, video) and "play here" for library titles, with resume. */
import { $, h, I, api, toast, events, episodeCode } from "./core.js";

const viewer = $("#viewer");
const stage = $("#viewerStage");
let session = null; // { video?, hls?, fileId?, itemId?, timer?, pool?, index?, onTv? }
let nextTimer = null;
let hlsLoading = null;

export const isOpen = () => !viewer.hidden;

function saveProgress() {
  const s = session;
  if (!s || !s.fileId || !s.video) return;
  const { currentTime, duration } = s.video;
  if (!duration || !Number.isFinite(duration) || currentTime < 5) return;
  api("/api/library/progress", { method: "POST", json: { file_id: s.fileId, position: currentTime, duration } }).catch(() => {});
}

function reset() {
  clearInterval(nextTimer);
  $("#viewerNextEp").hidden = true;
  if (!session) return;
  saveProgress();
  clearInterval(session.timer);
  if (session.hls) { try { session.hls.destroy(); } catch { /* already destroyed */ } }
  stage.querySelectorAll("video, audio").forEach((media) => { media.pause(); media.removeAttribute("src"); media.load(); });
  stage.replaceChildren();
  session = null;
}

export function close() {
  reset();
  viewer.hidden = true;
  document.body.classList.remove("playing");
  events.emit("viewer-closed");
}

function loadHls() {
  if (window.Hls) return Promise.resolve(window.Hls);
  hlsLoading = hlsLoading || new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "/vendor/hls.min.js";
    script.onload = () => resolve(window.Hls);
    script.onerror = reject;
    document.head.append(script);
  });
  return hlsLoading;
}

export function playVideo({ src, title, subtitle = "", fileId = "", itemId = "", start = 0, onTv = null }) {
  reset();
  const video = h("video", { controls: true, autoplay: true, playsinline: true, preload: "auto" });
  stage.append(video);
  session = { video, fileId, itemId, timer: null, hls: null, onTv };
  $("#viewerName").textContent = title;
  $("#viewerSub").textContent = subtitle;
  $("#viewerPrev").hidden = true;
  $("#viewerNext").hidden = true;
  $("#viewerTv").hidden = !onTv;
  viewer.hidden = false;
  document.body.classList.add("playing");

  if (/\.m3u8(\?|$)/i.test(src) && !video.canPlayType("application/vnd.apple.mpegurl")) {
    loadHls().then((Hls) => {
      if (!session || session.video !== video) return;
      const hls = new Hls({ enableWorker: true });
      hls.loadSource(src);
      hls.attachMedia(video);
      session.hls = hls;
    }).catch(() => toast("Lecteur HLS indisponible.", "err"));
  } else {
    video.src = src;
  }
  if (start > 5) video.addEventListener("loadedmetadata", () => { video.currentTime = start; }, { once: true });
  if (fileId) {
    session.timer = setInterval(saveProgress, 10000);
    video.addEventListener("pause", saveProgress);
    video.addEventListener("ended", () => ended(fileId, itemId));
  }
  video.addEventListener("error", () => {
    if (session && session.video === video) {
      toast("Ce navigateur ne sait pas lire ce fichier (codec). Lance-le sur la TV : Aura le lit avec mpv.", "err");
    }
  });
}

async function ended(fileId, itemId) {
  let result;
  try { result = await api("/api/library/ended", { method: "POST", json: { file_id: fileId } }); } catch { return; }
  events.emit("library-progress");
  if (!result.next || !session) return;
  const next = result.next;
  const label = `${episodeCode(next.season, next.episode)}${next.title ? ` · ${next.title}` : ""}`;
  let left = 8;
  const count = h("span", { class: "num" }, String(left));
  const start = () => {
    clearInterval(nextTimer);
    events.emit("play-here", { itemId, fileId: next.file_id, position: 0 });
  };
  const box = $("#viewerNextEp");
  box.replaceChildren(
    h("div", { class: "vn-kicker" }, "Épisode suivant"),
    h("div", { class: "vn-title" }, label),
    h("div", { class: "vn-actions" },
      h("button", { class: "btn primary", onclick: start }, I("play"), "Lire", result.autoplay ? [" dans ", count] : null),
      h("button", { class: "btn ghost", onclick: () => { clearInterval(nextTimer); box.hidden = true; } }, "Plus tard")),
  );
  box.hidden = false;
  if (result.autoplay) {
    nextTimer = setInterval(() => {
      left -= 1;
      count.textContent = String(left);
      if (left <= 0) start();
    }, 1000);
  }
}

/* ---------------------------------------------------------------- file previews */
function showAt(index) {
  if (!session || !session.pool) return;
  const pool = session.pool;
  if (index < 0 || index >= pool.length) return;
  session.index = index;
  stage.querySelectorAll("video, audio").forEach((media) => { media.pause(); media.removeAttribute("src"); });
  const item = pool[index];
  let el;
  if (item.kind === "video") el = h("video", { controls: true, autoplay: true, playsinline: true });
  else if (item.kind === "audio") el = h("audio", { controls: true, autoplay: true });
  else el = h("img", { alt: item.name });
  if (item.kind === "video") el.addEventListener("error", () => toast("Format non lisible dans le navigateur : télécharge le fichier, ou lance le film depuis la Bibliothèque.", "err"));
  el.src = item.url;
  stage.replaceChildren(el);
  $("#viewerName").textContent = item.name;
  $("#viewerSub").textContent = pool.length > 1 ? `${index + 1} / ${pool.length}` : "";
  $("#viewerPrev").hidden = pool.length < 2;
  $("#viewerNext").hidden = pool.length < 2;
  $("#viewerTv").hidden = true;
}

export function openMedia(pool, index) {
  reset();
  session = { pool, index };
  viewer.hidden = false;
  showAt(index);
}

export function handleKey(event) {
  if (viewer.hidden) return false;
  if (event.key === "Escape") { close(); return true; }
  if (session && session.pool) {
    if (event.key === "ArrowLeft") { showAt(session.index - 1); return true; }
    if (event.key === "ArrowRight") { showAt(session.index + 1); return true; }
  }
  return false;
}

$("#viewerClose").addEventListener("click", close);
$("#viewerPrev").addEventListener("click", () => session && showAt(session.index - 1));
$("#viewerNext").addEventListener("click", () => session && showAt(session.index + 1));
$("#viewerTv").addEventListener("click", () => {
  if (!session || !session.onTv) return;
  const position = session.video ? session.video.currentTime : 0;
  const handoff = session.onTv;
  close();
  handoff(position);
});
viewer.addEventListener("click", (event) => { if (event.target === viewer) close(); });
