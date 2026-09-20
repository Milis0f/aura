import json
import sqlite3
import time

import httpx
import pyotp
import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from aura import cli, db
from aura.main import create_app
from aura.services import accounts, library_scan
from aura.services.torrents import QBittorrent, TorrentError

PASSWORD = "correct-horse-battery"
LAN = ("192.168.1.30", 50000)
INTERNET = {"X-Forwarded-For": "8.8.8.8"}


@pytest.fixture
def media(tmp_path, monkeypatch):
    monkeypatch.setattr(library_scan, "MIN_BYTES", 10)
    root = tmp_path / "Disque"
    (root / "Films").mkdir(parents=True)
    (root / "Films" / "Heat.1995.1080p.mkv").write_bytes(bytes(2048))
    (root / "notes.txt").write_text("bonjour", encoding="utf-8")
    db.set_setting("library_dirs", json.dumps([{"label": "Disque", "path": str(root)}]))
    return root


def _ready(client: TestClient) -> TestClient:
    client.portal.call(client.app.state.aura.library.settle)
    return client


@pytest.fixture
def local(media):
    with TestClient(create_app()) as client:
        yield _ready(client)


@pytest.fixture
def lan(media):
    with TestClient(create_app(), client=LAN) as client:
        yield _ready(client)


def _owner(client: TestClient) -> dict[str, str]:
    r = client.post("/api/setup-owner", data={"username": "matteo", "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf"]}


def test_first_account_login_and_logout(local):
    me = local.get("/api/me").json()
    assert me["authenticated"] is False and me["can_setup"] is True
    _owner(local)
    assert local.get("/api/me").json()["username"] == "matteo"
    assert local.post("/api/setup-owner", data={"username": "intrus", "password": PASSWORD}).status_code == 409
    assert local.post("/api/logout").json()["ok"]
    assert local.get("/api/me").json()["authenticated"] is False
    r = local.post("/api/login", data={"username": "MATTEO", "password": PASSWORD})
    cookie = r.headers["set-cookie"].lower()
    assert r.status_code == 200 and r.json()["can_write"] is True
    assert "httponly" in cookie and "samesite=lax" in cookie and "secure" not in cookie


def test_internet_needs_an_account(media):
    with TestClient(create_app()) as client:
        _ready(client)
        assert client.post("/api/setup-owner", data={"username": "x", "password": PASSWORD}, headers=INTERNET).status_code == 403
        assert client.get("/api/me", headers=INTERNET).json()["can_setup"] is False
        assert client.get("/api/channels", headers=INTERNET).status_code == 401
        assert client.get("/api/library/home", headers=INTERNET).status_code == 401
        assert client.get("/tv/", headers=INTERNET, follow_redirects=False).status_code == 303
        assert client.get("/", headers=INTERNET).status_code == 200
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/ws?role=remote", headers=INTERNET):
                pass
        _owner(client)
        assert client.get("/api/library/home", headers=INTERNET).status_code == 200


def test_lockout_and_two_factor(local):
    _owner(local)
    local.post("/api/logout")
    for _ in range(8):
        assert local.post("/api/login", data={"username": "matteo", "password": "wrong-password-x"}).status_code == 401
    assert local.post("/api/login", data={"username": "matteo", "password": PASSWORD}).status_code == 429
    accounts.reset_failures()
    csrf = {"X-CSRF-Token": local.post("/api/login", data={"username": "matteo", "password": PASSWORD}).json()["csrf"]}
    assert local.post("/api/account/2fa/start").status_code == 403
    secret = local.post("/api/account/2fa/start", headers=csrf).json()["secret"]
    stale = pyotp.TOTP(secret).at(time.time() - 3600)
    assert local.post("/api/account/2fa/enable", data={"code": stale}, headers=csrf).status_code == 400
    assert local.post("/api/account/2fa/enable", data={"code": pyotp.TOTP(secret).now()}, headers=csrf).json()["ok"]
    local.post("/api/logout")
    refused = local.post("/api/login", data={"username": "matteo", "password": PASSWORD})
    assert refused.status_code == 401 and "6 chiffres" in refused.json()["detail"]
    signed = local.post("/api/login", data={"username": "matteo", "password": PASSWORD, "totp": pyotp.TOTP(secret).now()})
    assert signed.status_code == 200 and signed.json()["has_2fa"] is True
    assert len(local.get("/api/account/sessions").json()) == 1


def test_file_manager(local, media):
    csrf = _owner(local)
    assert "Disque" in local.get("/api/fs/roots").json()["roots"]
    listing = local.get("/api/fs/list", params={"root": "Disque"}).json()
    assert [i["name"] for i in listing["items"]] == ["Films", "notes.txt"]
    assert local.get("/api/fs/list", params={"root": "Disque", "path": "../.."}).status_code == 403

    upload = {"data": {"root": "Disque", "path": "Films"}, "files": {"file": ("../evasion.txt", b"hello")}}
    assert local.post("/api/fs/upload", **upload).status_code == 403
    assert local.post("/api/fs/upload", headers=csrf, **upload).json()["name"] == "evasion.txt"
    assert (media / "Films" / "evasion.txt").read_bytes() == b"hello"

    assert local.post("/api/fs/mkdir", data={"root": "Disque", "name": "Séries"}, headers=csrf).json()["ok"]
    assert local.post("/api/fs/rename", data={"root": "Disque", "name": "notes.txt", "new_name": "lisezmoi.txt"}, headers=csrf).json()["ok"]
    moved = local.post("/api/fs/move", data={"root": "Disque", "names": "lisezmoi.txt", "dest": "Séries"}, headers=csrf).json()
    assert moved["moved"] == 1 and (media / "Séries" / "lisezmoi.txt").exists()
    assert local.get("/api/fs/search", params={"root": "Disque", "q": "lisez"}).json()["items"][0]["dir"] == "Séries"
    part = local.get("/api/fs/stream", params={"root": "Disque", "path": "Séries/lisezmoi.txt"}, headers={"Range": "bytes=0-2"})
    assert part.status_code == 206 and part.content == b"bon"
    assert local.post("/api/fs/delete", data={"root": "Disque", "path": "Séries", "names": "lisezmoi.txt"}, headers=csrf).json()["removed"] == 1
    assert local.get("/api/stats").json()["disks"]

    accounts.create_user("invite", PASSWORD)
    local.post("/api/logout")
    guest = {"X-CSRF-Token": local.post("/api/login", data={"username": "invite", "password": PASSWORD}).json()["csrf"]}
    assert local.post("/api/fs/mkdir", data={"root": "Disque", "name": "X"}, headers=guest).status_code == 403


def test_library_on_the_home_network(lan):
    home = lan.get("/api/library/home").json()
    assert home["counts"]["films"] == 1
    heat = home["rows"][0]["items"][0]
    assert (heat["title"], heat["year"], heat["quality"]) == ("Heat", "1995", "1080p")
    file_id = lan.get(f"/api/library/items/{heat['id']}").json()["versions"][0]["id"]

    assert lan.get(f"/api/library/stream/{file_id}").status_code == 401
    played = lan.post("/api/library/play", json={"item_id": heat["id"]}).json()
    assert played["backend"] == "browser" and played["item"]["play_url"] == f"/api/library/stream/{file_id}"
    assert lan.get("/api/player").json()["state"]["item_id"] == "lib:" + file_id
    assert lan.post("/api/library/progress", json={"file_id": file_id, "position": 120, "duration": 5400}).json()["finished"] is False
    assert lan.get("/api/library/home").json()["rows"][0]["key"] == "continue"

    (drive,) = lan.get("/api/drives").json()["drives"]
    assert (drive["label"], drive["films"], drive["kind"]) == ("Disque", 1, "folder")
    link = lan.post("/api/library/links", json={"url": "https://cdn.example.org/Metropolis.1927.mp4"}).json()
    assert (link["title"], link["year"]) == ("Metropolis", "1927")
    assert lan.post("/api/library/folders", json={"path": "/"}).status_code == 401


def test_the_kiosk_reads_files_without_an_account(local):
    heat = local.get("/api/library/items", params={"kind": "film"}).json()["items"][0]
    file_id = local.get(f"/api/library/items/{heat['id']}").json()["versions"][0]["id"]
    video = local.get(f"/api/library/stream/{file_id}", headers={"Range": "bytes=0-99"})
    assert video.status_code == 206 and len(video.content) == 100 and video.headers["content-type"] == "video/x-matroska"


def test_security_headers(local):
    r = local.get("/tv/")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"


def test_downloads_through_qbittorrent(local):
    csrf = _owner(local)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/v2/torrents/info":
            return httpx.Response(200, json=[{"hash": "a" * 40, "name": "Metropolis", "progress": 0.5, "state": "stoppedDL"}])
        if request.url.path == "/api/v2/torrents/stop":
            return httpx.Response(404)  # qBittorrent 4.x only knows /pause
        return httpx.Response(200, text="Ok.")

    st = local.app.state.aura
    previous, st.qbit = st.qbit, QBittorrent("http://qb.test", transport=httpx.MockTransport(handler))
    if previous:
        local.portal.call(previous.aclose)
    assert local.get("/api/torrents").json()[0]["progress"] == 50.0
    assert local.post("/api/torrents/add", data={"magnet": "magnet:?xt=urn:btih:" + "a" * 40}, headers=csrf).json()["ok"]
    assert local.post("/api/torrents/action", data={"hashes": "a" * 40, "do": "pause"}, headers=csrf).json()["ok"]
    assert calls[-2:] == ["/api/v2/torrents/stop", "/api/v2/torrents/pause"]
    assert local.post("/api/torrents/add", data={"magnet": "javascript:alert(1)"}, headers=csrf).status_code == 400


def test_import_nasdash_accounts(tmp_path):
    legacy = tmp_path / "nasdash.db"
    con = sqlite3.connect(legacy)
    con.executescript(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, totp_secret TEXT,"
        " can_write INTEGER NOT NULL DEFAULT 0, can_torrent INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);"
        "CREATE TABLE audit (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, username TEXT, ip TEXT, action TEXT NOT NULL, detail TEXT);"
    )
    con.execute("INSERT INTO users(username, password_hash, can_write, can_torrent, created_at) VALUES (?,?,1,1,?)", ("Matteo", PasswordHasher().hash(PASSWORD), "2026-08-31T12:00:00+00:00"))
    con.execute("INSERT INTO audit(ts, username, ip, action) VALUES ('2026-08-31T12:00:00+00:00', 'Matteo', '192.168.1.192', 'login_ok')")
    con.commit()
    con.close()
    assert cli.main(["import-nasdash", str(legacy)]) == 0
    token, session = accounts.login("matteo", PASSWORD, "", "127.0.0.1", "pytest")
    assert session.can_torrent and accounts.session_from_token(token).username == "Matteo"
    assert cli.main(["import-nasdash", str(legacy)]) == 0 and accounts.user_count() == 1


async def test_unreachable_qbittorrent_names_the_address():
    """A wrong port is the usual cause, so the error has to say which address Aura tried."""

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = QBittorrent("http://127.0.0.1:8080", transport=httpx.MockTransport(refuse))
    try:
        with pytest.raises(TorrentError) as raised:
            await client.list()
    finally:
        await client.aclose()
    assert "http://127.0.0.1:8080" in raised.value.message
