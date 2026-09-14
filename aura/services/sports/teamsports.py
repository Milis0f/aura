"""Football, basketball, rugby, hockey schedules from TheSportsDB (free tier, no key required).

The free tier caps every response at a handful of rows, so coverage is built from two angles:
the next fixture of each competition the user is likely to care about, and the next few days of
fixtures per sport. Results are merged and de-duplicated on event id.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ...models import SportsEvent, stable_id

log = logging.getLogger(__name__)

BASE = "https://www.thesportsdb.com/api/v1/json/3"

# (league id, display name, our sport key)
LEAGUES: tuple[tuple[str, str, str], ...] = (
    ("4334", "Ligue 1", "football"),
    ("4328", "Premier League", "football"),
    ("4480", "Ligue des Champions", "football"),
    ("4335", "Liga", "football"),
    ("4332", "Serie A", "football"),
    ("4331", "Bundesliga", "football"),
    ("4387", "NBA", "basketball"),
    ("4391", "NFL", "other"),
    ("4380", "NHL", "other"),
    ("4446", "Rugby URC", "other"),
)

DAY_SPORTS: tuple[tuple[str, str], ...] = (("Soccer", "football"), ("Basketball", "basketball"))
DAYS_AHEAD = 3

SPORT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "football": ("canal+ sport", "canal+ foot", "bein sports", "rmc sport", "ligue 1", "prime video", "dazn", "eurosport"),
    "basketball": ("bein sports", "nba", "canal+ sport", "skweek"),
    "other": ("canal+ sport", "bein sports", "rmc sport", "eurosport", "sport en france"),
}


def _ts(event: dict[str, Any]) -> int:
    stamp = (event.get("strTimestamp") or "").strip()
    if stamp:
        try:
            return int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            pass
    date, time_ = (event.get("dateEvent") or "").strip(), (event.get("strTime") or "00:00:00").strip()
    if not date:
        return 0
    try:
        return int(datetime.fromisoformat(f"{date}T{time_[:8]}").replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return 0


def to_event(raw: dict[str, Any], sport: str, league_label: str = "") -> SportsEvent | None:
    name = (raw.get("strEvent") or "").strip()
    if not name:
        return None
    start = _ts(raw)
    league = league_label or (raw.get("strLeague") or "").strip()
    duration = 2 * 3600 if sport == "football" else 3 * 3600
    keywords = SPORT_KEYWORDS.get(sport, ("sport",))
    teams = tuple(t.lower() for t in (raw.get("strHomeTeam"), raw.get("strAwayTeam")) if t)
    return SportsEvent(
        id=stable_id("tsdb", str(raw.get("idEvent") or name), str(start)),
        sport=sport,
        name=name,
        session=league or "Match",
        start=start,
        end=start + duration if start else 0,
        location=(raw.get("strVenue") or "").strip(),
        url=f"https://www.thesportsdb.com/event/{raw.get('idEvent')}" if raw.get("idEvent") else "",
        keywords=keywords + teams + ((league.lower(),) if league else ()),
    )


async def _get(http: httpx.AsyncClient, path: str, **params: Any) -> list[dict[str, Any]]:
    try:
        r = await http.get(f"{BASE}/{path}", params=params, timeout=12)
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.debug("thesportsdb %s failed: %s", path, exc)
        return []
    return list(data.get("events") or [])


async def fetch(http: httpx.AsyncClient) -> list[SportsEvent]:
    """Next fixture per competition plus the next few days per sport, merged."""
    today = datetime.now(timezone.utc).date()
    jobs = [_get(http, "eventsnextleague.php", id=lid) for lid, _, _ in LEAGUES]
    jobs += [
        _get(http, "eventsday.php", d=(today + timedelta(days=d)).isoformat(), s=api_sport)
        for d in range(DAYS_AHEAD)
        for api_sport, _ in DAY_SPORTS
    ]
    results = await asyncio.gather(*jobs, return_exceptions=True)

    events: dict[str, SportsEvent] = {}

    def add(raws: Any, sport: str, label: str = "") -> None:
        if isinstance(raws, BaseException):
            return
        for raw in raws:
            ev = to_event(raw, sport, label)
            if ev:
                events.setdefault(ev.id, ev)

    for (lid, label, sport), raws in zip(LEAGUES, results[: len(LEAGUES)]):
        add(raws, sport, label)
    day_results = results[len(LEAGUES) :]
    i = 0
    for _day in range(DAYS_AHEAD):
        for _api_sport, sport in DAY_SPORTS:
            add(day_results[i] if i < len(day_results) else [], sport)
            i += 1
    return sorted(events.values(), key=lambda e: (e.start == 0, e.start))
