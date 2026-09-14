"""Formula 1 calendar via Jolpica (Ergast successor). Free, no key.

GET https://api.jolpi.ca/ergast/f1/{season}.json?limit=100
Each race has date/time (UTC) plus FirstPractice/SecondPractice/ThirdPractice/Qualifying/
Sprint/SprintQualifying sub-objects with their own date/time.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from ...models import SportsEvent, stable_id

log = logging.getLogger(__name__)

JOLPICA = "https://api.jolpi.ca/ergast/f1/{season}.json?limit=100"

SESSIONS: tuple[tuple[str, str], ...] = (
    ("FirstPractice", "Essais libres 1"),
    ("SecondPractice", "Essais libres 2"),
    ("ThirdPractice", "Essais libres 3"),
    ("SprintQualifying", "Qualifications Sprint"),
    ("Sprint", "Sprint"),
    ("Qualifying", "Qualifications"),
)

DURATIONS = {
    "Essais libres 1": 3600,
    "Essais libres 2": 3600,
    "Essais libres 3": 3600,
    "Qualifications Sprint": 2700,
    "Sprint": 2700,
    "Qualifications": 3600,
    "Course": 2 * 3600 + 1800,
}

KEYWORDS: tuple[str, ...] = (
    "formula 1",
    "formule 1",
    "f1",
    "grand prix",
    "canal+ f1",
    "canal+ sport",
    "sky sports f1",
    "f1 tv",
    "rtbf",
    "servus",
    "orf",
    "viaplay",
)


def _ts(date: str, time_: str) -> int:
    if not date:
        return 0
    t = (time_ or "00:00:00Z").replace("Z", "+00:00")
    try:
        return int(datetime.fromisoformat(f"{date}T{t}").astimezone(timezone.utc).timestamp())
    except ValueError:
        return 0


def races_to_events(payload: dict[str, Any]) -> list[SportsEvent]:
    races = payload.get("MRData", {}).get("RaceTable", {}).get("Races", [])
    out: list[SportsEvent] = []
    for r in races:
        gp = r.get("raceName", "Grand Prix")
        circuit = r.get("Circuit", {})
        loc = circuit.get("Location", {})
        location = ", ".join(x for x in (circuit.get("circuitName"), loc.get("locality"), loc.get("country")) if x)
        rnd = str(r.get("round", ""))
        sessions: list[tuple[str, int]] = []
        for key, label in SESSIONS:
            s = r.get(key)
            if s:
                sessions.append((label, _ts(s.get("date", ""), s.get("time", ""))))
        sessions.append(("Course", _ts(r.get("date", ""), r.get("time", ""))))
        for label, start in sessions:
            out.append(
                SportsEvent(
                    id=stable_id("f1", rnd, label, r.get("season", "")),
                    sport="f1",
                    name=gp,
                    session=label,
                    start=start,
                    end=start + DURATIONS.get(label, 3600) if start else 0,
                    location=location,
                    url=r.get("url", ""),
                    round=rnd,
                    keywords=KEYWORDS + (gp.lower().replace(" grand prix", ""),),
                )
            )
    return sorted(out, key=lambda e: e.start)


async def fetch_season(http: httpx.AsyncClient, season: int | str = "current") -> list[SportsEvent]:
    r = await http.get(JOLPICA.format(season=season), timeout=20)
    r.raise_for_status()
    return races_to_events(r.json())
