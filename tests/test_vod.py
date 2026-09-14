import pytest
from fastapi.testclient import TestClient

from aura.main import create_app
from aura.services import archive_films, catalog, m3u_parser, tmdb, xtream
from aura.services.textutil import normalize

VOD = """#EXTM3U
#EXTINF:-1 tvg-logo="http://p/1.jpg" group-title="Action",FR - Mad Max Fury Road (2015) 1080p
http://host/movie/u/p/1.mkv
#EXTINF:-1 group-title="Action",Heat (1995)
http://host/movie/u/p/2.mp4
#EXTINF:-1 group-title="Comédie",Intouchables (2011)
http://host/movie/u/p/3.mp4
#EXTINF:-1 group-title="Séries",Breaking Bad
http://host/series/u/p/9.mp4
"""


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def _seed():
    src = catalog.add_source("m3u_url", "VOD", "http://example/v.m3u")
    catalog.replace_channels(src.id, m3u_parser.parse_m3u(VOD, src.id).items)
    return src


def test_clean_title():
    assert tmdb.clean_title("FR - Mad Max Fury Road (2015) 1080p") == ("Mad Max Fury Road", "2015")
    assert tmdb.clean_title("Heat") == ("Heat", "")
    assert tmdb.clean_title("Double.Indemnity.1944.720p.BrRip.x265.HEVCBay.com.mkv") == ("Double Indemnity", "1944")
    assert tmdb.clean_title("Le.Cinquieme.Element.1997.MULTI.1080p.BluRay.x264-GROUP") == ("Le Cinquieme Element", "1997")
    assert tmdb.clean_title("Blade Runner 2049 (2017)") == ("Blade Runner 2049", "2017")


def test_archive_items_and_file_pick():
    docs = [{"identifier": "abc", "title": ["Night of the Living Dead"], "year": "1968", "description": "<p>Zombies</p>", "downloads": 10, "runtime": "1:36:00", "creator": "George A. Romero"}, {"identifier": "", "title": "x"}]
    items = archive_films.docs_to_items(docs, "s")
    assert len(items) == 1
    it = items[0]
    assert it.url == "archive://abc" and it.kind == "vod" and it.logo.endswith("/img/abc")
    assert it.extra["year"] == "1968" and it.extra["plot"] == "Zombies" and it.extra["director"] == "George A. Romero"
    files = [{"name": "a.ogv", "format": "Ogg Video", "size": "9"}, {"name": "a_512kb.mp4", "format": "512Kb MPEG4", "size": "5"}, {"name": "a.mp4", "format": "h.264", "size": "50"}]
    assert archive_films.pick_file(files) == "a.mp4"
    assert archive_films.pick_file([{"name": "x.avi"}]) == ""


def test_xtream_info_to_meta():
    info = {"name": "Heat", "releasedate": "1995-12-15", "movie_image": "http://p.jpg", "backdrop_path": ["http://b.jpg"], "plot": "Bank heist.", "rating": "8.2", "genre": "Crime, Drama", "duration": "02:50:00", "cast": "Al Pacino, Robert De Niro", "director": "Michael Mann", "youtube_trailer": "abc123", "tmdb_id": 949}
    m = xtream.info_to_meta(info)
    assert m["year"] == "1995" and m["backdrop"] == "http://b.jpg" and m["genres"] == ["Crime", "Drama"]
    assert m["runtime"] == 170 and m["cast"][0] == "Al Pacino" and m["trailer"].endswith("abc123") and m["tmdb_id"] == 949
    assert xtream.info_to_meta({}) == {}
    assert xtream._minutes("105 min") == 105 and xtream._minutes(6300) == 105 and xtream._minutes("") == 0


def test_vod_home_browse_details(client):
    _seed()
    home = client.get("/api/vod/home?kind=vod").json()
    assert home["total"] == 3 and {g["name"] for g in home["groups"]} == {"Action", "Comédie"}
    assert any(r["key"].startswith("group:") for r in home["rows"])
    assert home["hero"]["kind"] == "vod"
    br = client.get("/api/vod/browse?kind=vod&group=Action&sort=name").json()
    assert br["total"] == 2 and [i["name"] for i in br["items"]][0] == "Heat (1995)"  # sort ignores the "FR -" prefix
    assert client.get("/api/vod/browse?kind=vod&q=intouch").json()["total"] == 1
    mad = br["items"][1]
    d = client.get("/api/vod/" + mad["id"]).json()
    assert d["meta"]["title"] and d["meta"]["poster"] == "http://p/1.jpg"  # provider poster wins over keyless fallbacks
    assert [s["name"] for s in d["similar"]] == ["Heat (1995)"]
    assert client.get("/api/vod/nope").status_code == 404
    series = client.get("/api/vod/home?kind=series").json()
    assert series["total"] == 1


def test_vod_home_empty(client):
    d = client.get("/api/vod/home?kind=vod").json()
    assert d["total"] == 0 and d["rows"] == [] and d["hero"] is None


def test_posters_endpoint_without_key(client):
    _seed()
    ids = [i["id"] for i in client.get("/api/vod/browse?kind=vod").json()["items"]]
    r = client.post("/api/vod/posters", json={"ids": ids}).json()
    assert "posters" in r  # fallbacks may be empty offline; endpoint must not fail


def test_free_bundle_archive_registered(client):
    b = client.get("/api/sources").json()["bundles"]
    assert any(x["key"] == "archive-films" and x.get("type") == "archive" for x in b)


def test_normalize_keeps_digits():
    assert normalize("Heat (1995)") == "heat1995"
