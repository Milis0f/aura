"""Searching indexers: normalisation, safety of the links, merging, and a source that fails."""

import httpx
import pytest

from dataclasses import replace

from aura.config import SETTINGS
from aura.services import torrent_search

ARCHIVE_PAYLOAD = {
    "response": {
        "docs": [
            {"identifier": "Sintel", "title": "Sintel (Blender open movie)", "item_size": 1234, "publicdate": "2010-10-01T00:00:00Z"},
            {"title": "no identifier, skipped"},
        ]
    }
}

JACKETT_PAYLOAD = {
    "Results": [
        {
            "Title": "ubuntu-26.04-desktop-amd64.iso",
            "Size": 5_368_709_120,
            "Seeders": 1200,
            "Peers": 40,
            "PublishDate": "2026-04-25T09:00:00Z",
            "Link": "http://127.0.0.1:9117/dl/ubuntu.torrent",
            "Tracker": "Ubuntu",
        },
        {"Title": "hostile entry", "Link": "javascript:alert(1)"},
    ]
}


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _settings(monkeypatch, url: str = "", key: str = "") -> None:
    """Settings are frozen: swap the whole object the service reads."""
    monkeypatch.setattr(torrent_search, "SETTINGS", replace(SETTINGS, indexer_url=url, indexer_key=key))


async def test_public_catalogue_needs_no_configuration(monkeypatch):
    _settings(monkeypatch)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=ARCHIVE_PAYLOAD)

    async with _client(handler) as http:
        answer = await torrent_search.search(http, "sintel", limit=10)

    assert [source["key"] for source in answer["sources"]] == ["archive"]
    assert "archive.org/advancedsearch.php" in seen[0] and "rows=10" in seen[0]
    (result,) = answer["results"]
    assert result["torrent_url"] == "https://archive.org/download/Sintel/Sintel_archive.torrent"
    assert result["details_url"] == "https://archive.org/details/Sintel"
    # The Archive publishes no swarm counts: an invented number would sort above real ones.
    assert result["seeders"] is None
    assert answer["errors"] == []


async def test_self_hosted_indexer_is_merged_and_sanitised(monkeypatch):
    _settings(monkeypatch, "http://127.0.0.1:9117", "secret")

    def handler(request: httpx.Request) -> httpx.Response:
        if "archive.org" in request.url.host:
            return httpx.Response(200, json=ARCHIVE_PAYLOAD)
        assert request.url.params["apikey"] == "secret"
        assert "/api/v2.0/indexers/all/results" in request.url.path
        return httpx.Response(200, json=JACKETT_PAYLOAD)

    async with _client(handler) as http:
        answer = await torrent_search.search(http, "ubuntu", limit=10)

    names = [result["name"] for result in answer["results"]]
    assert names[0] == "ubuntu-26.04-desktop-amd64.iso"  # 1200 seeders sorts above the unknown swarm
    assert "hostile entry" not in names  # javascript: link, nothing to hand to qBittorrent
    assert [source["key"] for source in answer["sources"]] == ["archive", "indexer"]


async def test_a_failing_source_is_reported_not_swallowed(monkeypatch):
    _settings(monkeypatch, "http://127.0.0.1:9117", "secret")

    def handler(request: httpx.Request) -> httpx.Response:
        if "archive.org" in request.url.host:
            return httpx.Response(200, json=ARCHIVE_PAYLOAD)
        return httpx.Response(403, json={"error": "nope"})

    async with _client(handler) as http:
        answer = await torrent_search.search(http, "ubuntu")

    assert len(answer["results"]) == 1  # the public catalogue still answered
    assert answer["errors"][0]["message"] == "clé d'API refusée (403)"


async def test_short_queries_never_reach_an_indexer(monkeypatch):
    _settings(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("no request should be sent for a one-letter query")

    async with _client(handler) as http:
        answer = await torrent_search.search(http, " a ")

    assert answer == {"results": [], "sources": [{"key": "archive", "name": "Internet Archive", "kind": "public"}], "errors": []}


@pytest.mark.parametrize(
    "raw,expected",
    [
        ({"title": "x", "magnet": "magnet:?xt=urn:btih:abc"}, "magnet:?xt=urn:btih:abc"),
        ({"title": "x", "magnet": "MAGNET:?xt=urn:btih:abc"}, "MAGNET:?xt=urn:btih:abc"),
        ({"title": "x", "magnet": "javascript:alert(1)", "link": "https://ok.test/a.torrent"}, ""),
    ],
)
def test_only_real_magnets_survive(raw, expected):
    assert torrent_search.normalise(raw)["magnet"] == expected


def test_duplicates_keep_the_healthiest_copy():
    rows = [
        {"name": "same", "size": 10, "magnet": "magnet:?xt=urn:btih:dup", "torrent_url": "", "seeders": 5},
        {"name": "same", "size": 10, "magnet": "magnet:?xt=urn:btih:dup", "torrent_url": "", "seeders": 90},
    ]
    (kept,) = torrent_search.dedupe(rows)
    assert kept["seeders"] == 90


def test_the_same_release_from_two_indexers_collapses_on_its_title():
    """Two trackers, two spellings, one release: the healthiest copy wins and the other disappears."""
    rows = [
        {"name": "Big.Buck.Bunny.2008.1080p.WEB.x264-GROUP", "size": 2_000_000_000,
         "magnet": "magnet:?xt=urn:btih:aaa", "torrent_url": "", "seeders": 3},
        {"name": "Big Buck Bunny (2008) 1080p WEB x264-GROUP", "size": 2_000_100_000,
         "magnet": "magnet:?xt=urn:btih:bbb", "torrent_url": "", "seeders": 120},
    ]
    (kept,) = torrent_search.dedupe(rows)
    assert kept["seeders"] == 120


def test_two_qualities_of_one_film_stay_two_results():
    rows = [
        {"name": "Big.Buck.Bunny.2008.720p", "size": 800_000_000, "magnet": "magnet:?xt=urn:btih:aaa", "torrent_url": "", "seeders": 9},
        {"name": "Big.Buck.Bunny.2008.2160p", "size": 9_000_000_000, "magnet": "magnet:?xt=urn:btih:bbb", "torrent_url": "", "seeders": 9},
    ]
    assert len(torrent_search.dedupe(rows)) == 2


def test_the_box_only_remembers_what_it_returned():
    torrent_search._RECENT.clear()
    torrent_search.remember([
        {"id": "keep", "name": "Sintel", "torrent_url": "https://archive.test/s.torrent"},
        {"id": "drop", "name": "Magnet only", "torrent_url": ""},
    ])
    assert torrent_search.recall("keep")[0] == "https://archive.test/s.torrent"
    assert torrent_search.recall("drop") is None
    assert torrent_search.recall("never-seen") is None
