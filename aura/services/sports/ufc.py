"""UFC / MMA / boxing schedules.

There is no free official UFC API. We scrape https://www.ufc.com/events (tolerant regexes on the
event cards, each carrying a unix timestamp for the main card) and fall back to EPG keyword search.
Boxing has no single reliable free source: events are discovered from the EPG only.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any

import httpx

from ...models import SportsEvent, stable_id

log = logging.getLogger(__name__)

UFC_EVENTS = "https://www.ufc.com/events"

UFC_KEYWORDS: tuple[str, ...] = ("ufc", "mma", "rmc sport", "dazn", "espn", "canal+ sport", "fight night")
BOXING_KEYWORDS: tuple[str, ...] = ("boxe", "boxing", "wbc", "wba", "ibf", "wbo", "dazn", "canal+ sport", "rmc sport")

_HEADLINE = re.compile(
    r'<h3 class="c-card-event--result__headline">\s*<a href="(?P<href>[^"]+)">(?P<title>.*?)</a>', re.S
)
_PREFIX = re.compile(r'c-card-event--result__prefix">\s*<a[^>]*>(.*?)</a>', re.S)
_TS = re.compile(r'data-main-card-timestamp="(?P<ts>\d+)"')
_LOC = re.compile(r'c-card-event--result__location">.*?<h5>(?P<loc>.*?)</h5>', re.S)
_TAG = re.compile(r"<[^>]+>")


_SLUG_NUM = re.compile(r"ufc-(\d{2,3})(?:-|$)")


def prefix_from_slug(href: str) -> str:
    """'/event/cryptocom-ufc-331' -> 'UFC 331', '/event/ufc-fight-night-...' -> 'UFC Fight Night'."""
    slug = href.rsplit("/", 1)[-1].lower()
    m = _SLUG_NUM.search(slug)
    if m:
        return f"UFC {m.group(1)}"
    if "fight-night" in slug:
        return "UFC Fight Night"
    if "ufc" in slug:
        return "UFC"
    return ""


def _text(s: str) -> str:
    return html.unescape(_TAG.sub("", s or "")).strip()


def parse_ufc_events(page: str) -> list[SportsEvent]:
    out: list[SportsEvent] = []
    for m in _HEADLINE.finditer(page):
        title = _text(m.group("title"))
        href = m.group("href")
        before = page[max(0, m.start() - 2000) : m.start()]
        after = page[m.end() : m.end() + 4000]
        ts_m = _TS.search(after)
        if not ts_m:
            continue
        start = int(ts_m.group("ts"))
        prefix_m = _PREFIX.search(before)
        prefix = _text(prefix_m.group(1)) if prefix_m else prefix_from_slug(href)
        loc_m = _LOC.search(after)
        loc = _text(loc_m.group("loc")) if loc_m else ""
        name = f"{prefix}: {title}" if prefix and prefix.lower() not in title.lower() else title
        out.append(
            SportsEvent(
                id=stable_id("ufc", href),
                sport="ufc",
                name=name or "UFC",
                session="Main Card",
                start=start,
                end=start + 3 * 3600,
                location=loc,
                url=f"https://www.ufc.com{href}" if href.startswith("/") else href,
                keywords=UFC_KEYWORDS + tuple(w for w in (prefix.lower(),) if w),
            )
        )
    return sorted(out, key=lambda e: e.start)


async def fetch_ufc(http: httpx.AsyncClient) -> list[SportsEvent]:
    try:
        r = await http.get(UFC_EVENTS, timeout=25, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("UFC fetch failed: %s", exc)
        return []
    events = parse_ufc_events(r.text)
    if not events:
        log.warning("UFC page parsed but no events found (markup changed?)")
    return events


def epg_events(rows: list[dict[str, Any]], sport: str, keywords: tuple[str, ...]) -> list[SportsEvent]:
    """Turn EPG programme rows into SportsEvents (dedup by title+start)."""
    seen: set[tuple[str, int]] = set()
    out: list[SportsEvent] = []
    for r in rows:
        key = (r["title"].lower(), r["start"] // 600)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            SportsEvent(
                id=stable_id(sport, r["title"], str(r["start"])),
                sport=sport,
                name=r["title"],
                session=r.get("category") or "Direct",
                start=r["start"],
                end=r["stop"],
                keywords=keywords,
            )
        )
    return out
