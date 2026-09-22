/* Aura web — the thing you just asked for goes where downloads live.

   A download starts on one side of the screen and lands in a rail icon that may be a thousand pixels
   away; without a link between the two, the click feels like nothing happened. So the card collapses
   into a small disc that arcs into the downloads icon, and the icon takes the hit.

   The disc is a throwaway element on top of everything: it never touches layout, and if anything about
   it fails the download still went through. */
import { $ } from "./core.js";

const FLIGHT_MS = 620;

export function flyToDownloads(source) {
  const target = $('.rail-btn[data-view="downloads"] .i') || $('.tab[data-view="downloads"] .i');
  if (!source || !target || !source.isConnected) return bump(target);
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return bump(target);

  const from = source.getBoundingClientRect();
  const to = target.getBoundingClientRect();
  if (!from.width || !to.width) return bump(target);

  const disc = document.createElement("div");
  disc.className = "dl-fly";
  disc.style.cssText =
    `left:${from.left + from.width / 2}px;top:${from.top + from.height / 2}px;` +
    `width:${Math.min(from.width, 120)}px;height:${Math.min(from.width, 120)}px`;
  document.body.append(disc);

  const dx = to.left + to.width / 2 - (from.left + from.width / 2);
  const dy = to.top + to.height / 2 - (from.top + from.height / 2);
  // Two keyframes with a lifted midpoint: a straight line reads as a glitch, an arc reads as a throw.
  const flight = disc.animate(
    [
      { transform: "translate(-50%, -50%) scale(1)", opacity: 0.9 },
      { transform: `translate(calc(-50% + ${dx * 0.55}px), calc(-50% + ${dy * 0.55 - 70}px)) scale(0.42)`, opacity: 1, offset: 0.55 },
      { transform: `translate(calc(-50% + ${dx}px), calc(-50% + ${dy}px)) scale(0.12)`, opacity: 0.35 },
    ],
    { duration: FLIGHT_MS, easing: "cubic-bezier(0.4, 0, 0.2, 1)", fill: "forwards" },
  );
  flight.onfinish = () => { disc.remove(); bump(target); };
  flight.oncancel = () => disc.remove();
  setTimeout(() => { if (disc.isConnected) { disc.remove(); bump(target); } }, FLIGHT_MS + 250);
}

/** The icon acknowledges the arrival, even when the flight could not run. */
function bump(target) {
  const button = target ? target.closest(".rail-btn, .tab") : null;
  if (!button) return;
  button.classList.remove("dl-land");
  void button.offsetWidth; // restart the animation when two downloads land in a row
  button.classList.add("dl-land");
  setTimeout(() => button.classList.remove("dl-land"), 700);
}
