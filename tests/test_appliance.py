"""Guards for the appliance layer: OS files agree with the app, the drives library reaches every screen,
system partitions stay out of reach, and posters stay light enough for a Mac mini."""

import re
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from aura import config
from aura.main import create_app
from aura.services import metadata
from aura.services.storage import Volume

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_os_files_use_the_app_port(monkeypatch):
    monkeypatch.delenv("AURA_PORT", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    port = str(config.load_settings().port)
    assert f"Environment=AURA_PORT={port}" in _read("os/systemd/aura.service")
    assert f"AURA_PORT={port}" in _read("os/install.sh")
    kiosk = _read("os/systemd/aura-kiosk.service") + _read("os/scripts/kiosk-session.sh")
    assert set(re.findall(r"127\.0\.0\.1:(\d+)", kiosk)) == {port}
    assert set(re.findall(r"proxy_pass http://127\.0\.0\.1:(\d+)", _read("os/nginx-aura.conf"))) == {port}


def test_installer_ships_what_the_drives_library_needs():
    script = _read("os/install.sh")
    for package in ("udisks2", "ffmpeg", "ntfs-3g", "exfatprogs", "polkitd"):
        assert re.search(rf"\b{re.escape(package)}\b", script), package
    rules = _read("os/polkit/50-aura-udisks.rules")
    assert "org.freedesktop.udisks2.filesystem-mount" in rules and "org.freedesktop.udisks2.power-off-drive" in rules
    assert "EnvironmentFile=-/etc/aura.env" in _read("os/systemd/aura.service")
    assert "os/polkit/*.rules" in _read("os/scripts/update.sh")


def test_system_partitions_cannot_be_mounted_or_ejected():
    with TestClient(create_app()) as client:
        jobs = client.app.state.aura.library
        client.portal.call(jobs.settle)
        jobs.watcher.volumes = [Volume(id="uuid:efi", label="EFI", kind="disk", device="/dev/sda1", fstype="vfat", system=True)]
        assert client.post("/api/drives/uuid:efi/mount").status_code == 404
        assert client.post("/api/drives/uuid:efi/eject").status_code == 404
        assert all(d["id"] != "uuid:efi" for d in client.get("/api/drives").json()["drives"])


def test_tv_home_and_setup_count_the_drives_library():
    with TestClient(create_app()) as client:
        assert set(client.get("/api/home").json()["library"]) == {"counts", "resume", "recent"}
        assert set(client.get("/api/setup").json()["library"]) == {"films", "series", "links", "episodes"}
        page = client.get("/tv/").text
        assert page.index('src="tv-library.js"') < page.index('src="tv.js"')
        assert "/library/items" in client.get("/remote/remote.js").text


async def test_series_posters_use_the_card_sized_image():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "name": "Dark", "premiered": "2017-12-01", "summary": "<p>Time travel</p>", "genres": ["Drama"],
            "image": {"medium": "https://img.test/medium.jpg", "original": "https://img.test/original.jpg"},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        show = await metadata.tvmaze_show(http, "Dark")
    assert show["poster"] == "https://img.test/medium.jpg" and show["overview"] == "Time travel"


def test_wikipedia_posters_are_resized_thumbnails():
    summary = {
        "thumbnail": {"source": "https://upload.wikimedia.org/wikipedia/fr/thumb/a/ab/Affiche.jpg/320px-Affiche.jpg"},
        "originalimage": {"source": "https://upload.wikimedia.org/wikipedia/fr/a/ab/Affiche.jpg"},
    }
    assert metadata._wiki_image(summary).endswith("/500px-Affiche.jpg")
    assert metadata._wiki_image({"originalimage": {"source": "https://img.test/o.jpg"}}) == "https://img.test/o.jpg"
