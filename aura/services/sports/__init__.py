"""Sports aggregator: F1 (Jolpica), UFC (scrape), boxing (EPG), cached in the DB."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from ... import db
from ...config import SETTINGS
from ...models import SportsEvent
from .. import epg
from . import f1, teamsports, ufc

log = logging.getLogger(__name__)

CACHE_KEY = "sports:events"


def _from_dict(d: dict[str, Any]) -> SportsEvent:
    return SportsEvent(**{**d, "keywords": tuple(d.get("keywords") or ())})


async def refresh(http: httpx.AsyncClient) -> list[SportsEvent]:
    events: list[SportsEvent] = []
    try:
        events += await f1.fetch_season(http)
    except Exception as exc:  # noqa: BLE001
        log.warning("F1 calendar failed: %s", exc)
    events += await ufc.fetch_ufc(http)
    try:
        events += await teamsports.fetch(http)
    except Exception as exc:  # noqa: BLE001
        log.warning("team sports calendar failed: %s", exc)
    db.cache_set(CACHE_KEY, [e.to_dict() for e in events])
    db.set_setting("sports_last_refresh", str(int(time.time())))
    return events


def cached() -> list[SportsEvent]:
    data = db.cache_get(CACHE_KEY, SETTINGS.sports_refresh_hours * 3600 * 4)  # keep stale while refresh fails
    return [_from_dict(d) for d in (data or [])]


def upcoming(sport: str | None = None, days: int = 45, include_past_hours: int = 6) -> list[SportsEvent]:
    now = int(time.time())
    lo, hi = now - include_past_hours * 3600, now + days * 86400
    evs = [e for e in cached() if (sport is None or e.sport == sport) and (e.start == 0 or lo <= e.start <= hi)]
    # EPG-discovered events (boxing always, UFC as fallback)
    horizon = now + 14 * 86400
    if sport in (None, "boxing"):
        rows = epg.search_programmes(["boxe", "boxing", "wbc", "wba", "ibf", "wbo"], now - 3600, horizon)
        evs += ufc.epg_events(rows, "boxing", ufc.BOXING_KEYWORDS)
    if sport in (None, "ufc") and not any(e.sport == "ufc" for e in evs):
        rows = epg.search_programmes(["ufc", "mma"], now - 3600, horizon)
        evs += ufc.epg_events(rows, "ufc", ufc.UFC_KEYWORDS)
    if sport in (None, "other"):
        rows = epg.search_programmes(
            ["top 14", "roland garros", "moto gp", "motogp", "tour de france", "jeux olympiques"],
            now - 3600,
            now + 3 * 86400,
        )
        evs += ufc.epg_events(rows, "other", ("sport",))
    return sorted(evs, key=lambda e: (e.start == 0, e.start))


def find(event_id: str) -> SportsEvent | None:
    return next((e for e in upcoming(days=400, include_past_hours=24 * 30) if e.id == event_id), None)


def status_of(e: SportsEvent, now: int | None = None) -> str:
    now = now or int(time.time())
    if not e.start:
        return "tba"
    if now < e.start:
        return "upcoming"
    if now <= (e.end or e.start + 3 * 3600):
        return "live"
    return "past"
