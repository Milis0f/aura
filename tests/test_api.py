import pytest
from fastapi.testclient import TestClient

from aura.main import create_app
from aura.services import catalog, m3u_parser

SAMPLE = """#EXTM3U
#EXTINF:-1 tvg-id="TF1.fr" group-title="France",TF1
http://host/live/1.m3u8
#EXTINF:-1 group-title="Sports",Canal+ Sport
http://host/live/2.m3u8
#EXTINF:-1 group-title="Films",Interstellar
http://host/movie/3.mp4
"""


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def _seed():
    src = catalog.add_source("m3u_url", "Test", "http://example/x.m3u")
    catalog.replace_channels(src.id, m3u_parser.parse_m3u(SAMPLE, src.id).items)
    return src


def test_health_and_static(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/tv/").status_code == 200
    assert client.get("/remote/").status_code == 200
    assert client.get("/").status_code == 200  # the web app (NAS side)


def test_setup_and_home_unconfigured(client):
    s = client.get("/api/setup").json()
    assert s["configured"] is False
    h = client.get("/api/home").json()
    assert h["configured"] is False and h["apps"]


def test_channels_groups_search_favorites(client):
    _seed()
    r = client.get("/api/channels", params={"kind": "live"}).json()
    assert len(r["items"]) == 2
    assert {g["name"] for g in r["groups"]} == {"France", "Sports"}
    assert client.get("/api/channels", params={"kind": "vod"}).json()["items"][0]["name"] == "Interstellar"
    s = client.get("/api/search", params={"q": "canal"}).json()
    assert s["live"][0]["name"] == "Canal+ Sport"
    cid = s["live"][0]["id"]
    assert client.post(f"/api/favorites/{cid}").json()["favorite"] is True
    assert client.get("/api/favorites").json()["items"][0]["id"] == cid
    assert client.post(f"/api/favorites/{cid}").json()["favorite"] is False


def test_play_and_player_state(client):
    _seed()
    cid = client.get("/api/channels", params={"kind": "live"}).json()["items"][0]["id"]
    r = client.post("/api/play", json={"channel_id": cid}).json()
    assert r["state"]["backend"] == "browser"
    assert r["item"]["play_url"].startswith("/api/proxy?url=")
    assert client.get("/api/player").json()["state"]["item_id"] == cid
    assert client.get("/api/history").json()["items"][0]["channel_id"] == cid
    assert client.post("/api/stop").json()["state"]["backend"] == "idle"


def test_play_validation(client):
    assert client.post("/api/play", json={}).status_code == 400
    assert client.post("/api/play", json={"channel_id": "nope"}).status_code == 404


def test_sources_validation(client):
    assert client.post("/api/sources", json={"type": "m3u_url", "url": "ftp://x"}).status_code == 400
    assert client.post("/api/sources", json={"type": "xtream", "url": "http://x"}).status_code == 400
    assert client.post("/api/sources", json={"type": "free", "bundle": "nope"}).status_code == 400


def test_settings_roundtrip(client):
    r = client.put("/api/settings", json={"values": {"tmdb_api_key": "abc", "device_name": "Salon"}}).json()
    assert r["settings"]["tmdb_api_key"] == "" and r["settings"]["tmdb_api_key_set"] == "1"
    assert r["settings"]["device_name"] == "Salon"
    assert client.put("/api/settings", json={"values": {"evil": "1"}}).status_code == 400


def test_mylist(client):
    r = client.post("/api/mylist", json={"title": "Test", "url": "http://x/y.mp4"}).json()
    assert client.get("/api/mylist").json()["items"][0]["id"] == r["id"]
    assert client.post("/api/mylist", json={"title": "Bad", "url": "file:///etc/passwd"}).status_code == 400


def test_proxy_rejects_bad_scheme(client):
    assert client.get("/api/proxy", params={"url": "file:///etc/passwd"}).status_code == 400


def test_network_mock(client):
    n = client.get("/api/network").json()
    assert n["urls"] and n["online"] is True
    assert client.get("/api/network/wifi").json()["networks"]


def test_websocket_hello(client):
    with client.websocket_connect("/api/ws?role=remote") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "hello" and msg["role"] == "remote"


def test_shared_design_assets_are_served(client):
    assert client.get("/shared/tokens.css").status_code == 200
    assert client.get("/shared/icons.js").status_code == 200
    font = client.get("/shared/fonts/geist-latin-wght.woff2")
    assert font.status_code == 200 and font.content[:4] == b"wOF2"


def test_ui_files_are_revalidated_after_updates(client):
    for path in ("/tv/tv.css", "/tv/tv.js", "/remote/remote.js", "/shared/tokens.css"):
        assert client.get(path).headers["cache-control"] == "no-cache", path


def test_port_prefers_aura_port_then_port(monkeypatch):
    from aura import config

    monkeypatch.delenv("AURA_PORT", raising=False)
    monkeypatch.setenv("PORT", "5173")
    assert config.load_settings().port == 5173
    monkeypatch.setenv("AURA_PORT", "8123")
    assert config.load_settings().port == 8123
    monkeypatch.delenv("AURA_PORT")
    monkeypatch.delenv("PORT")
    assert config.load_settings().port == 8000  # 8080 is qBittorrent on the NAS
