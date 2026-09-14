"""Performance and playback-path tests: bulk EPG, search ranking, source dedupe, probing, proxy,
source racing at play time, team sports parsing."""

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from aura import db
from aura.main import create_app
from aura.services import catalog, epg, m3u_parser, probe
from aura.services.sports import teamsports


def _xt(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M%S +0000")


def _seed_playlist(text: str, url: str) -> str:
    src = catalog.add_source("m3u_url", url, url)
    catalog.replace_channels(src.id, m3u_parser.parse_m3u(text, src.id).items)
    return src.id


# ---------------------------------------------------------------- bulk EPG

def test_bulk_epg_matches_per_channel_lookup():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    xml = f"""<?xml version="1.0"?><tv>
      <channel id="TF1.fr"><display-name>TF1</display-name></channel>
      <channel id="CS.fr"><display-name>Canal+ Sport</display-name></channel>
      <programme start="{_xt(now - timedelta(minutes=30))}" stop="{_xt(now + timedelta(minutes=30))}" channel="TF1.fr"><title>JT</title></programme>
      <programme start="{_xt(now + timedelta(minutes=30))}" stop="{_xt(now + timedelta(minutes=90))}" channel="TF1.fr"><title>Film</title></programme>
      <programme start="{_xt(now - timedelta(minutes=10))}" stop="{_xt(now + timedelta(minutes=50))}" channel="CS.fr"><title>Ligue 1</title></programme>
    </tv>""".encode()
    epg.store_epg("t", xml)
    _seed_playlist(
        '#EXTM3U\n#EXTINF:-1 tvg-id="TF1.fr",TF1\nhttp://h/1.m3u8\n'
        "#EXTINF:-1,Canal+ Sport HD\nhttp://h/2.m3u8\n#EXTINF:-1,Nothing Here\nhttp://h/3.m3u8\n",
        "http://example/a.m3u",
    )
    chs = catalog.channels("live")
    bulk = {c["name"]: c for c in catalog.with_epg(chs)}
    for c in chs:
        eid = epg.resolve_epg_id(c["tvg_id"], c["name"]) or ""
        single = epg.now_next(eid) if eid else []
        assert bulk[c["name"]]["epg_id"] == eid
        assert (bulk[c["name"]]["now"] or {}).get("title") == (single[0]["title"] if single else None)
    assert bulk["TF1"]["now"]["title"] == "JT" and bulk["TF1"]["next"]["title"] == "Film"
    assert bulk["Canal+ Sport HD"]["now"]["title"] == "Ligue 1"
    assert bulk["Nothing Here"]["now"] is None


def test_with_epg_passes_non_live_through():
    vod = {"id": "v", "kind": "vod", "name": "Film", "tvg_id": ""}
    assert catalog.with_epg([vod]) == [vod]


# ---------------------------------------------------------------- catalogue behaviour

def test_search_ranks_exact_and_prefix_matches_first():
    _seed_playlist(
        "#EXTM3U\n#EXTINF:-1,Culture en France Magazine\nhttp://h/a.m3u8\n"
        "#EXTINF:-1,3sat France Edition\nhttp://h/b.m3u8\n"
        "#EXTINF:-1,France 24\nhttp://h/c.m3u8\n#EXTINF:-1,France\nhttp://h/d.m3u8\n",
        "http://example/rank.m3u",
    )
    names = [c["name"] for c in catalog.channels("live", q="france")]
    assert names[0] == "France"
    assert names[1] == "France 24"
    assert set(names[2:]) == {"Culture en France Magazine", "3sat France Edition"}


def test_same_playlist_is_not_registered_twice():
    a = catalog.add_source("m3u_url", "One", "http://example/same.m3u")
    b = catalog.add_source("m3u_url", "Two", "http://example/same.m3u")
    assert a.id == b.id
    assert len(catalog.list_sources()) == 1


def test_dedupe_sources_removes_existing_duplicates():
    first = catalog.add_source("m3u_url", "Keep", "http://example/dup.m3u")
    db.execute(
        "INSERT INTO sources(id,type,name,url,enabled,created) VALUES('dup2','m3u_url','Copy','http://example/dup.m3u',1,?)",
        (10**10,),
    )
    assert catalog.dedupe_sources() == 1
    assert [s.id for s in catalog.list_sources()] == [first.id]


def test_db_reuses_one_connection_per_thread():
    assert db.connect() is db.connect()


# ---------------------------------------------------------------- probing

def _mock_handler(counter: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if counter is not None:
            counter[request.url.host] = counter.get(request.url.host, 0) + 1
        host = request.url.host
        if host == "dead.test":
            return httpx.Response(404)
        if host == "html.test":
            return httpx.Response(200, text="<html>blocked</html>")
        if host == "cors.test":
            return httpx.Response(200, text="#EXTM3U\n", headers={"access-control-allow-origin": "*"})
        if host == "seg.test":
            return httpx.Response(206, content=b"\x47" * 188, headers={"content-type": "video/mp2t"})
        return httpx.Response(200, text="#EXTM3U\n")

    return handler


def test_probe_race_orders_alive_first_and_detects_cors():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_mock_handler())) as http:
            return await probe.race(
                http,
                ["http://dead.test/a.m3u8", "http://html.test/b.m3u8", "http://cors.test/c.m3u8", "http://plain.test/d.m3u8", "http://seg.test/e.ts"],
                use_cache=False,
            )

    res = asyncio.run(run())
    assert [r.ok for r in res] == [True, True, True, False, False]
    by_host = {httpx.URL(r.url).host: r for r in res}
    assert by_host["cors.test"].cors is True
    assert by_host["plain.test"].cors is False
    assert by_host["seg.test"].ok is True
    assert by_host["dead.test"].error == "HTTP 404"
    assert by_host["html.test"].ok is False  # an HTML error page is not a playlist


def test_probe_uses_cache_for_recent_verdicts():
    counter: dict = {}

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_mock_handler(counter))) as http:
            await probe.probe_url(http, "http://cors.test/live.m3u8")
            await probe.probe_url(http, "http://cors.test/live.m3u8")

    asyncio.run(run())
    assert counter["cors.test"] == 1


# ---------------------------------------------------------------- proxy + play racing (through the API)

@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def test_proxy_rewrites_playlists_and_caches_segments(client):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".m3u8"):
            return httpx.Response(200, text="#EXTM3U\n#EXTINF:6,\nseg1.ts\n", headers={"content-type": "application/vnd.apple.mpegurl"})
        # a streamed body, like a real CDN response (in-memory bodies are pre-read by httpx)
        async def body():
            yield b"\x47" * 188
            yield b"\x47" * 188

        return httpx.Response(200, content=body(), headers={"content-type": "video/mp2t"})

    client.app.state.aura.stream_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    r = client.get("/api/proxy", params={"url": "http://cdn.test/live/index.m3u8"})
    assert r.status_code == 200
    assert "/api/proxy?url=http%3A%2F%2Fcdn.test%2Flive%2Fseg1.ts" in r.text
    assert r.headers["access-control-allow-origin"] == "*" and r.headers["cache-control"] == "no-store"
    seg = client.get("/api/proxy", params={"url": "http://cdn.test/live/seg1.ts"})
    assert seg.status_code == 200 and len(seg.content) == 376
    assert seg.headers["cache-control"] == "public, max-age=30"


def test_play_skips_dead_source_and_plays_direct_when_cors_allows(client):
    dead_src = _seed_playlist("#EXTM3U\n#EXTINF:-1,News One\nhttp://dead.test/live/a.m3u8\n", "http://example/dead.m3u")
    _seed_playlist("#EXTM3U\n#EXTINF:-1,News One\nhttp://cors.test/live/b.m3u8\n", "http://example/alive.m3u")
    dead_id = next(c["id"] for c in catalog.channels("live", source_id=dead_src))
    client.app.state.aura.http = httpx.AsyncClient(transport=httpx.MockTransport(_mock_handler()))
    r = client.post("/api/play", json={"channel_id": dead_id}).json()
    assert r["item"]["url"] == "http://cors.test/live/b.m3u8"
    assert r["item"]["direct"] is True and r["item"]["play_url"] == "http://cors.test/live/b.m3u8"
    assert r["token"] and {p["ok"] for p in r["probed"]} == {True, False}


def test_play_uses_proxy_when_origin_has_no_cors(client):
    _seed_playlist("#EXTM3U\n#EXTINF:-1,Plain\nhttp://plain.test/live/c.m3u8\n", "http://example/plain.m3u")
    cid = catalog.channels("live", q="plain")[0]["id"]
    client.app.state.aura.http = httpx.AsyncClient(transport=httpx.MockTransport(_mock_handler()))
    r = client.post("/api/play", json={"channel_id": cid}).json()
    assert r["item"]["direct"] is False and r["item"]["play_url"].startswith("/api/proxy?url=")


def test_fast_start_can_be_disabled(client):
    _seed_playlist("#EXTM3U\n#EXTINF:-1,Quick\nhttp://dead.test/live/q.m3u8\n", "http://example/q.m3u")
    cid = catalog.channels("live", q="quick")[0]["id"]
    client.put("/api/settings", json={"values": {"fast_start": "0"}})
    r = client.post("/api/play", json={"channel_id": cid}).json()
    assert r["probed"] == [] and r["item"]["url"] == "http://dead.test/live/q.m3u8"


def test_settings_accept_playback_keys(client):
    r = client.put("/api/settings", json={"values": {"live_backend": "mpv", "fast_start": "1"}}).json()
    assert r["settings"]["live_backend"] == "mpv" and r["settings"]["fast_start"] == "1"


# ---------------------------------------------------------------- team sports

def test_teamsports_event_mapping():
    raw = {
        "idEvent": "2489490", "strEvent": "Rennes vs Marseille", "strLeague": "French Ligue 1",
        "strTimestamp": "2026-09-11T18:45:00", "strVenue": "Roazhon Park", "strHomeTeam": "Rennes", "strAwayTeam": "Marseille",
    }
    ev = teamsports.to_event(raw, "football", "Ligue 1")
    assert ev.start == int(datetime(2026, 9, 11, 18, 45, tzinfo=timezone.utc).timestamp())
    assert ev.end - ev.start == 2 * 3600
    assert ev.session == "Ligue 1" and ev.location == "Roazhon Park"
    assert "rennes" in ev.keywords and "marseille" in ev.keywords and "bein sports" in ev.keywords
    assert teamsports.to_event({"strEvent": ""}, "football") is None
    no_stamp = teamsports.to_event({"strEvent": "A vs B", "dateEvent": "2026-09-12", "strTime": "20:00:00"}, "basketball")
    assert no_stamp.start == int(datetime(2026, 9, 12, 20, 0, tzinfo=timezone.utc).timestamp())


def test_teamsports_fetch_merges_duplicates():
    fixture = {"idEvent": "1", "strEvent": "PSG vs Lyon", "strTimestamp": "2026-09-13T19:00:00", "strLeague": "French Ligue 1"}

    def handler(request: httpx.Request) -> httpx.Response:
        if "eventsnextleague" in request.url.path and request.url.params.get("id") == "4334":
            return httpx.Response(200, json={"events": [fixture]})
        if "eventsday" in request.url.path and request.url.params.get("s") == "Soccer":
            return httpx.Response(200, json={"events": [fixture]})
        return httpx.Response(200, json={"events": None})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await teamsports.fetch(http)

    events = asyncio.run(run())
    assert [e.name for e in events] == ["PSG vs Lyon"]
    assert events[0].session == "Ligue 1"  # the competition label wins over the raw league name


# ---------------------------------------------------------------- names: search key vs channel identity

def test_normalize_and_fold_rules():
    from aura.services.textutil import fold, normalize

    assert normalize("TF1 FR HD") == "tf1"
    assert normalize("France 2") == "france2"
    assert normalize("FR - Mad Max (2015)") == "madmax2015"
    assert normalize("beIN Sports 1 [Geo-blocked] (720p)") == "beinsports1"
    assert normalize("Journal de 20h") == "journalde20h"
    assert normalize("France 24 Ⓨ") == "france24"
    assert fold("Télé Loisirs Ⓨ") == "tele loisirs"
    assert fold("Че") == "че"


def test_search_does_not_match_everything_for_country_words():
    _seed_playlist(
        "#EXTM3U\n#EXTINF:-1,Че\nhttp://h/ru.m3u8\n#EXTINF:-1,СТВ\nhttp://h/ru2.m3u8\n"
        "#EXTINF:-1,France 24 French (1080p)\nhttp://h/f24.m3u8\n#EXTINF:-1,Culture en France\nhttp://h/cul.m3u8\n",
        "http://example/scripts.m3u",
    )
    assert [c["name"] for c in catalog.channels("live", q="france")] == ["France 24 French (1080p)", "Culture en France"]
    assert [c["name"] for c in catalog.channels("live", q="че")] == ["Че"]


def test_failover_never_mixes_language_feeds_or_repeats_a_url():
    _seed_playlist("#EXTM3U\n#EXTINF:-1,France 24 Ⓨ\nhttp://a/1.m3u8\n#EXTINF:-1,TF1 HD\nhttp://a/2.m3u8\n", "http://example/a1.m3u")
    _seed_playlist(
        "#EXTM3U\n#EXTINF:-1,France 24 العربية Ⓨ\nhttp://b/1.m3u8\n#EXTINF:-1,France 24\nhttp://b/2.m3u8\n"
        "#EXTINF:-1,TF1 FR\nhttp://b/3.m3u8\n#EXTINF:-1,TF1 HD\nhttp://a/2.m3u8\n",
        "http://example/b1.m3u",
    )
    f24 = next(c for c in catalog.channels("live", q="france 24") if c["name"] == "France 24 Ⓨ")
    assert [c["name"] for c in catalog.alternatives(f24)] == ["France 24"]
    tf1 = next(c for c in catalog.channels("live", q="tf1") if c["url"] == "http://a/2.m3u8")
    assert [c["url"] for c in catalog.alternatives(tf1)] == ["http://b/3.m3u8"]


def test_migrate_names_recomputes_stale_keys_once():
    src = _seed_playlist("#EXTM3U\n#EXTINF:-1,France 2 HD\nhttp://h/f2.m3u8\n", "http://example/mig.m3u")
    db.execute("UPDATE channels SET name_norm='2', name_fold='' WHERE source_id=?", (src,))
    db.set_setting("names_version", "1")
    assert catalog.migrate_names() == 1
    assert db.query_one("SELECT name_norm, name_fold FROM channels WHERE source_id=?", (src,)) == {"name_norm": "france2", "name_fold": "france 2 hd"}
    assert catalog.migrate_names() == 0


# ---------------------------------------------------------------- kiosk and hand-over of play orders

def test_kiosk_remembers_it_is_down_and_resets_when_tv_connects():
    import time as _time

    from aura.services.kiosk import Kiosk

    k = Kiosk(port=9)  # nothing listens on the discard port
    assert asyncio.run(k.available()) is False
    started = _time.perf_counter()
    assert asyncio.run(k.available()) is False
    assert _time.perf_counter() - started < 0.05  # cached verdict, no second connection attempt
    k.mark_up()
    assert k._down_until == 0.0


def test_play_order_is_delivered_when_the_tv_page_reconnects(client):
    _seed_playlist("#EXTM3U\n#EXTINF:-1,Later\nhttp://h/later.m3u8\n", "http://example/later.m3u")
    cid = catalog.channels("live", q="later")[0]["id"]
    client.put("/api/settings", json={"values": {"fast_start": "0"}})
    token = client.post("/api/play", json={"channel_id": cid}).json()["token"]
    with client.websocket_connect("/api/ws?role=tv") as ws:
        assert ws.receive_json()["type"] == "hello"
        order = ws.receive_json()
        assert order["type"] == "play" and order["token"] == token
    assert client.app.state.aura.pending_play is None


def test_probe_rejects_web_pages_served_as_streams():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "page.test":
            return httpx.Response(200, text="<!DOCTYPE html><html>channel page</html>", headers={"content-type": "text/html; charset=utf-8"})
        if request.url.host == "sniff.test":  # wrong content type, HTML body
            return httpx.Response(200, text="  <html><body>login</body></html>", headers={"content-type": "application/octet-stream"})
        return httpx.Response(200, content=bytes([0x47]) * 188, headers={"content-type": "video/mp2t"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await probe.race(http, ["http://page.test/c/news/live", "http://sniff.test/live/1", "http://ts.test/live/2"], use_cache=False)

    by_host = {httpx.URL(r.url).host: r for r in asyncio.run(run())}
    assert by_host["page.test"].ok is False and by_host["page.test"].error == "page web"
    assert by_host["sniff.test"].ok is False
    assert by_host["ts.test"].ok is True


# ---------------------------------------------------------------- first-bytes probing, format sniffing, YouTube pages

def test_probe_does_not_hang_on_endless_live_streams():
    import time as _time

    async def endless():
        while True:
            yield bytes([0x47]) * 1316
            await asyncio.sleep(0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=endless(), headers={"content-type": "video/mp2t"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            started = _time.perf_counter()
            res = await probe.probe_url(http, "http://live.test/live/u/p/1.ts", use_cache=False)
            return res, _time.perf_counter() - started

    res, elapsed = asyncio.run(run())
    assert res.ok is True and res.format == "mpegts"
    assert elapsed < 1.0


def test_probe_sniffs_hls_behind_extensionless_url():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="#EXTM3U\n#EXT-X-VERSION:3\n", headers={"content-type": "application/octet-stream"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await probe.probe_url(http, "http://iptv.test/live/playlist?token=1", use_cache=False)

    res = asyncio.run(run())
    assert res.ok is True and res.format == "hls"


def test_decide_backend_follows_probed_format(monkeypatch):
    from aura.services import player

    monkeypatch.setattr(player, "weak_hardware", lambda: True)
    monkeypatch.setattr(player.shutil, "which", lambda name: "/usr/bin/mpv")
    assert player.decide_backend("http://h/live/12345") == "mpv"
    assert player.decide_backend("http://h/live/12345", {"format": "hls"}) == "browser"
    assert player.decide_backend("http://h/live/12345", {"format": "mpegts"}) == "mpv"


def test_extract_manifest_follows_channel_redirect(monkeypatch):
    import sys
    import types

    from aura.services import resolver

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=False, process=True, **kwargs):
            if "/c/" in url:
                return {"_type": "url", "url": "https://www.youtube.com/watch?v=abc"}
            if url.endswith("off"):
                return {"formats": [{"protocol": "https", "url": "https://v/2.mp4"}]}
            return {"formats": [
                {"protocol": "m3u8_native", "url": "https://v/1.m3u8", "height": 360, "manifest_url": "https://m/master.m3u8"},
                {"protocol": "https", "url": "https://v/2.mp4", "height": 1080},
            ]}

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYDL))
    assert resolver._extract_manifest("https://www.youtube.com/c/news/live") == "https://m/master.m3u8"
    with pytest.raises(ValueError):
        resolver._extract_manifest("https://www.youtube.com/watch?v=off")
    assert resolver.needs_resolve("https://youtu.be/abc") and not resolver.needs_resolve("https://cdn.test/live.m3u8")


def test_youtube_pages_are_resolved_before_playback_and_cached(client, monkeypatch):
    from aura.services import resolver

    calls = []

    def fake_extract(url):
        calls.append(url)
        return "https://manifest.googlevideo.com/api/manifest/hls_variant/id/1/file/index.m3u8"

    monkeypatch.setattr(resolver, "_extract_manifest", fake_extract)
    _seed_playlist("#EXTM3U\n#EXTINF:-1,News Channel Ⓨ\nhttps://www.youtube.com/c/news/live\n", "http://example/yt.m3u")
    cid = catalog.channels("live", q="news channel")[0]["id"]
    client.app.state.aura.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, text="#EXTM3U\n")))
    first = client.post("/api/play", json={"channel_id": cid}).json()
    assert first["item"]["url"].startswith("https://manifest.googlevideo.com/")
    assert first["item"]["extra"]["format"] == "hls"
    assert first["item"]["extra"]["page_url"] == "https://www.youtube.com/c/news/live"
    assert first["item"]["backend"] == "browser"
    client.post("/api/play", json={"channel_id": cid})
    assert calls == ["https://www.youtube.com/c/news/live"]


def test_direct_stream_is_preferred_over_a_youtube_page(client, monkeypatch):
    from aura.services import resolver

    monkeypatch.setattr(resolver, "_extract_manifest", lambda url: pytest.fail("must not resolve when a stream answers"))
    _seed_playlist("#EXTM3U\n#EXTINF:-1,Info 24\nhttps://www.youtube.com/c/info24/live\n", "http://example/p1.m3u")
    _seed_playlist("#EXTM3U\n#EXTINF:-1,Info 24\nhttp://cors.test/live/info.m3u8\n", "http://example/p2.m3u")
    page = next(c for c in catalog.channels("live", q="info 24") if "youtube" in c["url"])
    client.app.state.aura.http = httpx.AsyncClient(transport=httpx.MockTransport(_mock_handler()))
    r = client.post("/api/play", json={"channel_id": page["id"]}).json()
    assert r["item"]["url"] == "http://cors.test/live/info.m3u8"
    assert all("youtube" not in a["url"] for a in r["alternatives"])


def test_dash_manifests_go_to_mpv():
    from aura.services.player import decide_backend

    assert decide_backend("http://h/live/manifest.mpd") == "mpv"


# ---------------------------------------------------------------- playlists that are not playlists

def test_parser_ignores_html_and_placeholders():
    html = "<!doctype html>\n<html lang=\"en\">\n<meta charset=\"utf-8\">\n</html>\n"
    assert m3u_parser.parse_m3u(html).items == ()
    text = "#EXTM3U\n#EXTINF:-1,Closed Channel\n[NO PUBLIC STREAM]\n#EXTINF:-1,Open Channel\nhttps://cdn.test/open.m3u8\n"
    assert [i.name for i in m3u_parser.parse_m3u(text).items] == ["Open Channel"]


def test_purge_invalid_channels_removes_scheme_less_rows():
    src = _seed_playlist("#EXTM3U\n#EXTINF:-1,Good\nhttp://h/good.m3u8\n", "http://example/purge.m3u")
    db.execute(
        "INSERT INTO channels(id,source_id,name,name_norm,name_fold,url,kind) VALUES('bad',?,'<html>','html','html','<html lang=en>','live')",
        (src,),
    )
    assert catalog.purge_invalid_channels() == 1
    assert [c["name"] for c in catalog.channels("live", source_id=src)] == ["Good"]


def test_refresh_rejects_a_web_page_instead_of_a_playlist():
    src = catalog.add_source("m3u_url", "Page", "http://pages.test/free-list/")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<!doctype html><html><body>free lists</body></html>", headers={"content-type": "text/html"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await catalog.refresh_source(http, src)

    refreshed = asyncio.run(run())
    assert refreshed.status.startswith("erreur") and "page web" in refreshed.status
    assert catalog.channels("live", source_id=src.id) == []


def test_html_lines_containing_links_are_not_streams():
    page = '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>' + chr(10) + '<img src="https://cdn.test/logo.png">' + chr(10)
    assert m3u_parser.parse_m3u(page).items == ()
    assert m3u_parser.is_stream_url("https://cdn.test/live/index.m3u8?token=a1")
    assert m3u_parser.is_stream_url("udp://@239.0.0.1:1234")
    assert not m3u_parser.is_stream_url('href="https://cdn.test/x"')
    assert not m3u_parser.is_stream_url("https://")


def test_purge_removes_html_rows_that_contain_a_link():
    src = _seed_playlist("#EXTM3U" + chr(10) + "#EXTINF:-1,Good" + chr(10) + "http://h/good.m3u8" + chr(10), "http://example/purge2.m3u")
    db.execute(
        "INSERT INTO channels(id,source_id,name,name_norm,name_fold,url,kind) VALUES('bad2',?,'x','x','x',?,'live')",
        (src, '<link href="https://fonts.gstatic.com" crossorigin>'),
    )
    assert catalog.purge_invalid_channels() == 1
    assert [c["name"] for c in catalog.channels("live", source_id=src)] == ["Good"]


def test_history_hides_entries_whose_channel_is_gone():
    nl = chr(10)
    src = _seed_playlist("#EXTM3U" + nl + "#EXTINF:-1,Kept" + nl + "http://h/kept.m3u8" + nl, "http://example/hist.m3u")
    kept = catalog.channels("live", source_id=src)[0]
    catalog.record_history(kept["id"], kept["name"], "live")
    catalog.record_history("ghost", "<meta name=viewport>", "live")
    assert [row["channel_id"] for row in catalog.history()] == [kept["id"]]
