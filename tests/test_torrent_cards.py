"""Grouping releases into catalogue cards, and what happens when the catalogue says nothing."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from aura.services import torrent_cards


def release(name: str, seeders: int | None = 10, size: int = 1_000_000_000, indexer: str = "Test") -> dict[str, Any]:
    return {
        "id": name, "name": name, "size": size, "seeders": seeders, "leechers": 0, "published": "",
        "torrent_url": f"https://example.org/{name}.torrent", "magnet": "", "details_url": "", "indexer": indexer,
    }


def test_releases_of_one_title_become_one_card():
    cards = torrent_cards.group([
        release("Big.Buck.Bunny.2008.1080p.WEB.x264-GROUP", seeders=4),
        release("Big Buck Bunny (2008) 2160p", seeders=40, size=8_000_000_000),
        release("Sintel.2010.720p", seeders=7),
    ])
    assert [card["title"] for card in cards] == ["Big Buck Bunny", "Sintel"]
    bunny = cards[0]
    assert len(bunny["releases"]) == 2
    assert bunny["year"] == "2008"
    # the card advertises its best release, and that release is the one offered first
    assert bunny["seeders"] == 40
    assert bunny["size"] == 8_000_000_000
    assert "2160p" in bunny["releases"][0]["name"]


def test_a_card_without_a_poster_still_has_something_to_draw():
    card = torrent_cards.group([release("Some.Obscure.Thing.2021.1080p")])[0]
    assert card["enriched"] is False
    assert card["poster"] == ""
    assert card["initials"] == "SO"
    assert 0 <= card["hue"] <= 359
    # the same title keeps the same colour between two searches
    assert card["hue"] == torrent_cards.group([release("Some.Obscure.Thing.2021.1080p")])[0]["hue"]


def test_episodes_are_asked_to_the_series_catalogue():
    assert torrent_cards.kind_of("Cosmos.S01E03.1080p") == "tv"
    assert torrent_cards.kind_of("Some Show Season 2 Complete") == "tv"
    assert torrent_cards.kind_of("Big Buck Bunny 2008 1080p") == "movie"


def test_slug_ignores_punctuation_and_case():
    assert torrent_cards.slug("The Matrix (1999)") == torrent_cards.slug("the.matrix.1999")
    assert torrent_cards.slug("!!!") == ""


def test_a_catalogue_that_never_answers_leaves_the_cards_usable(monkeypatch):
    """Enrichment is best effort: a hanging metadata source must not hang the search."""
    async def never(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        await asyncio.sleep(30)
        return {}

    monkeypatch.setattr(torrent_cards.tmdb, "api_key", lambda: "key")
    monkeypatch.setattr(torrent_cards.tmdb, "enrich", never)
    monkeypatch.setattr(torrent_cards, "ENRICH_SECONDS", 0.05)
    cards = torrent_cards.group([release("Big.Buck.Bunny.2008.1080p")])
    out = asyncio.run(torrent_cards.enrich(None, cards))
    assert out[0]["enriched"] is False
    assert out[0]["title"] == "Big Buck Bunny"


def test_found_titles_replace_the_release_name(monkeypatch):
    async def found(_http: Any, name: str, media: str) -> dict[str, Any]:
        assert media == "movie"
        assert "Bunny" in name
        return {"title": "Big Buck Bunny", "year": "2008", "poster": "https://img/p.jpg",
                "overview": "Un lapin.", "rating": 7.4, "genres": ["Animation", None]}

    monkeypatch.setattr(torrent_cards.tmdb, "api_key", lambda: "key")
    monkeypatch.setattr(torrent_cards.tmdb, "enrich", found)
    cards = asyncio.run(torrent_cards.enrich(None, torrent_cards.group([release("Big.Buck.Bunny.2008.1080p.WEB-GROUP")])))
    assert cards[0]["enriched"] is True
    assert cards[0]["overview"] == "Un lapin."
    assert cards[0]["poster"] == "https://img/p.jpg"
    assert cards[0]["genres"] == ["Animation"]


def test_without_a_tmdb_key_nothing_is_asked_of_anyone(monkeypatch):
    """The keyless fallbacks are rate-limited: a search must never fan out to them."""
    called = False

    async def boom(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(torrent_cards.tmdb, "api_key", lambda: "")
    monkeypatch.setattr(torrent_cards.tmdb, "enrich", boom)
    cards = asyncio.run(torrent_cards.enrich(None, torrent_cards.group([release("Sintel.2010.1080p")])))
    assert called is False
    assert cards[0]["enriched"] is False
    assert cards[0]["initials"] == "SI"


@pytest.mark.parametrize(("title", "expected"), [("Interstellar", "IN"), ("The Matrix", "TM"), ("", "?")])
def test_initials(title: str, expected: str):
    assert torrent_cards.initials(title) == expected
