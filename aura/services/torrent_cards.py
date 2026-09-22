"""Turn a flat list of releases into catalogue cards, and put a poster on each one.

Indexers answer with release names ("Big.Buck.Bunny.2008.1080p.WEB.x264-GROUP"), not with titles. Three
releases of the same film are three rows in a table but one card in a grid, so results are grouped by
normalised title and each card keeps its releases underneath — the grid shows titles, the sheet lets the
owner pick which release to download.

Cards are filled from TMDB, with the key and the cache Aura already uses for its library — no second API
to configure. TMDB only, on purpose: the keyless fallbacks (iTunes, Wikipedia, TVMaze) are meant for the
handful of titles a library scan resolves over hours, and asking them for two dozen cards on every search
earns the box an HTTP 429 that then breaks the library too. Without a TMDB key, and for anything the
catalogue does not know, the card still renders: initials on a gradient seeded by the title.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from typing import Any

import httpx

from ..core import settings
from . import tmdb

log = logging.getLogger(__name__)

# Declared in core.settings; these are the fallbacks the registry returns when nothing is stored.
ENRICH_BUDGET = 24  # cards that get a metadata lookup; the rest fall back to initials
ENRICH_PARALLEL = 6
ENRICH_SECONDS = 10.0

# Season/episode markers: 'S02E05', '1x04', 'Saison 2', 'Complete Series'.
_SERIES = re.compile(
    r"(?i)(?<![a-z0-9])(?:s\d{1,2}e\d{1,3}|s\d{1,2}(?![a-z0-9])|\d{1,2}x\d{2}|"
    r"seasons?[ ._-]*\d{1,2}|saisons?[ ._-]*\d{1,2}|complete[ ._-]*(?:series|seasons?)|integrale)"
)
_WORD = re.compile(r"[a-z0-9]+")


def kind_of(name: str) -> str:
    """'movie' or 'tv' — decides which catalogue is asked, nothing else."""
    return "tv" if _SERIES.search(name or "") else "movie"


def slug(title: str) -> str:
    """'The Matrix (1999)' and 'the.matrix.1999.1080p' collapse onto the same key."""
    return "".join(_WORD.findall((title or "").lower()))


def initials(title: str) -> str:
    words = [word for word in re.split(r"[^\w]+", title or "") if word]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][:1] + words[1][:1]).upper()


def hue(key: str) -> int:
    """A stable colour per title, so a card keeps the same gradient between searches."""
    return int(hashlib.sha1((key or "?").encode()).hexdigest()[:4], 16) % 360


def _rank(release: dict[str, Any]) -> tuple[int, int]:
    return (release.get("seeders") or -1, release.get("size") or 0)


def group(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One card per title, releases sorted best-swarm first, cards sorted by their best release."""
    cards: dict[str, dict[str, Any]] = {}
    for result in results:
        title, year = tmdb.clean_title(result.get("name", ""))
        key = slug(title) or slug(result.get("name", "")) or result.get("id", "")
        card = cards.get(key)
        if card is None:
            card = cards[key] = {
                "key": key,
                "title": title or result.get("name", ""),
                "year": year,
                "media": kind_of(result.get("name", "")),
                "releases": [],
                "poster": "", "backdrop": "", "overview": "", "rating": None, "genres": [],
                "initials": initials(title or result.get("name", "")),
                "hue": hue(key),
                "enriched": False,
            }
        elif year and not card["year"]:
            card["year"] = year
        card["releases"].append(result)

    for card in cards.values():
        card["releases"].sort(key=_rank, reverse=True)
        best = card["releases"][0]
        card["seeders"] = best.get("seeders")
        card["size"] = best.get("size")
        card["sources"] = sorted({r.get("indexer", "") for r in card["releases"] if r.get("indexer")})
    return sorted(cards.values(), key=lambda card: _rank(card["releases"][0]), reverse=True)


async def _fill(http: httpx.AsyncClient, card: dict[str, Any], gate: asyncio.Semaphore) -> None:
    async with gate:
        found = await tmdb.enrich(http, card["releases"][0]["name"], card["media"])
    if not found or not found.get("title"):
        return
    card.update({
        "title": found["title"],
        "year": found.get("year") or card["year"],
        "poster": found.get("poster") or "",
        "backdrop": found.get("backdrop") or "",
        "overview": found.get("overview") or "",
        "rating": found.get("rating"),
        "genres": [g for g in (found.get("genres") or []) if g][:3],
        "initials": initials(found["title"]),
        "enriched": True,
    })


async def enrich(http: httpx.AsyncClient, cards: list[dict[str, Any]], budget: int | None = None) -> list[dict[str, Any]]:
    """Best effort: no key, or a catalogue that is slow or down, leaves the cards on their fallback art."""
    if not tmdb.api_key():
        return cards
    # Read once per call, not per card: the registry is a dict lookup but a loop is a loop.
    if budget is None:
        budget = int(settings.get("cards.enrich_budget"))
    deadline = float(settings.get("cards.enrich_seconds"))
    gate = asyncio.Semaphore(int(settings.get("cards.enrich_parallel")))
    tasks = [_fill(http, card, gate) for card in cards[:budget]]
    if not tasks:
        return cards
    try:
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), deadline)
    except TimeoutError:
        log.info("card enrichment timed out after %.0fs", deadline)
    return cards
