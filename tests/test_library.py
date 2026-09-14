from pathlib import Path

import pytest

from aura.services import library, library_scan, storage


@pytest.fixture(autouse=True)
def small_files(monkeypatch):
    monkeypatch.setattr(library_scan, "MIN_BYTES", 10)
    monkeypatch.setattr(library_scan, "SAMPLE_BYTES", 1000)


def _file(root: Path, rel: str, size: int = 100) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(size))
    return path


def _drive(root: Path) -> storage.Volume:
    vol = storage.folder_volume(str(root), "Disque test")
    library.upsert_drive(vol, available=True)
    return vol


def _scan(vol: storage.Volume) -> library_scan.ScanReport:
    return library_scan.scan(vol.id, Path(vol.mountpoint))


def test_scan_finds_films_and_series(tmp_path):
    _file(tmp_path, "Films/Le.Grand.Bleu.1988.MULTI.1080p.BluRay.x264-FGT.mkv")
    _file(tmp_path, "Films/Inception (2010)/movie.mkv")
    _file(tmp_path, "Films/Inception (2010)/poster.jpg")
    _file(tmp_path, "Séries/Breaking Bad/Saison 1/01 - Pilot.mkv")
    _file(tmp_path, "Séries/Breaking Bad/Saison 1/02 - Cat's in the Bag.mkv")
    _file(tmp_path, "Séries/Breaking Bad/Saison 2/01 - Seven Thirty-Seven.mkv")
    _file(tmp_path, "DCIM/VID_20230514_183022.mp4")
    _file(tmp_path, "Films/VID_20230514_183022.mp4")
    _file(tmp_path, "Films/Le.Grand.Bleu.1988.sample.mkv", size=50)
    _file(tmp_path, "Downloads/incomplete/Tenet.2020.mkv")
    _file(tmp_path, "Films/Tenet.2020.mkv.part")
    vol = _drive(tmp_path)

    report = _scan(vol)
    assert (report.files, report.new_items) == (5, 3)
    assert library.finish_scan(vol.id) == {"films": 2, "episodes": 3}

    films, total = library.items(library.FILM)
    assert total == 2 and {f["title"] for f in films} == {"Le Grand Bleu", "Inception"}
    bleu = next(f for f in films if f["title"] == "Le Grand Bleu")
    assert (bleu["year"], bleu["quality"], bleu["langs"]) == ("1988", "1080p", ["MULTI"])
    inception = next(f for f in films if f["title"] == "Inception")
    assert inception["poster"] == f"/api/library/art/{inception['id']}"
    assert library.art_path(inception["id"]).name == "poster.jpg"
    (series,) = library.items(library.SERIES)[0]
    assert (series["title"], series["seasons"]) == ("Breaking Bad", 2)


def test_rescan_is_incremental_and_follows_deletions(tmp_path):
    heat = _file(tmp_path, "Films/Heat.1995.mkv")
    _file(tmp_path, "Films/Ronin.1998.mkv")
    vol = _drive(tmp_path)
    assert _scan(vol).changed == 2
    again = _scan(vol)
    assert (again.changed, again.removed, again.new_items) == (0, 0, 0)
    heat.unlink()
    assert _scan(vol).removed == 1
    assert [i["title"] for i in library.items(library.FILM)[0]] == ["Ronin"]


def test_an_empty_mount_point_never_wipes_the_library(tmp_path):
    film = _file(tmp_path, "Films/Heat.1995.mkv")
    vol = _drive(tmp_path)
    _scan(vol)
    film.unlink()
    (tmp_path / "Films").rmdir()
    with pytest.raises(library_scan.EmptyVolume):
        _scan(vol)
    assert library.items(library.FILM)[0]


def test_unplugged_drive_hides_titles_but_keeps_resume_points(tmp_path):
    _file(tmp_path, "Films/Heat.1995.mkv")
    vol = _drive(tmp_path)
    _scan(vol)
    heat = library.items(library.FILM)[0][0]
    start = library.default_file(heat["id"])
    library.save_progress(start["file_id"], 600, 6000)

    library.mark_unavailable_except([])
    assert library.items(library.FILM)[0] == []
    assert library.items(library.FILM, online_only=False)[0][0]["online"] is False
    assert library.default_file(heat["id"]) is None

    library.upsert_drive(vol, available=True)
    resumed = library.default_file(heat["id"])
    assert resumed["resume"] and resumed["position"] == 600
    assert library.continue_watching()[0]["resume"]["position"] == 600


def test_series_resume_and_next_episode(tmp_path):
    for rel in ("Séries/Dark/Saison 1/01.mkv", "Séries/Dark/Saison 1/02.mkv", "Séries/Dark/Saison 2/01.mkv"):
        _file(tmp_path, rel)
    vol = _drive(tmp_path)
    _scan(vol)
    dark = library.items(library.SERIES)[0][0]

    first = library.default_file(dark["id"])
    assert (first["season"], first["episode"]) == (1, 1)
    second = library.next_episode(first["file_id"])
    assert (second["season"], second["episode"]) == (1, 2)
    library.mark_finished(first["file_id"])
    after = library.default_file(dark["id"])
    assert (after["season"], after["episode"], after["resume"]) == (1, 2, False)
    third = library.next_episode(second["id"])
    assert (third["season"], third["episode"]) == (2, 1)
    assert library.next_episode(third["id"]) is None

    detail = library.item_detail(dark["id"])
    assert [s["season"] for s in detail["seasons_list"]] == [1, 2]
    assert detail["seasons_list"][0]["episodes"][0]["finished"] is True
    assert library.continue_watching()[0]["resume"]["episode"] == 2


def test_links_are_films_with_an_address():
    item = library.add_link("https://cdn.example.org/films/Le%20Voyage%20dans%20la%20Lune%20(1902).mp4")
    assert (item["kind"], item["title"], item["year"], item["online"]) == ("link", "Le Voyage dans la Lune", "1902", True)
    assert library.default_file(item["id"])["file_id"] == "link:" + item["id"]
    assert library.save_progress("link:" + item["id"], 300, 900)["finished"] is False
    with pytest.raises(ValueError):
        library.add_link("file:///etc/passwd")
    assert library.remove_link(item["id"]) and not library.remove_link(item["id"])


def test_home_rows(tmp_path):
    for year, name in enumerate(("Heat", "Ronin", "Collateral", "Thief", "Sicario", "Drive", "Prisoners"), start=1990):
        _file(tmp_path, f"Films/{name}.{year}.mkv")
    vol = _drive(tmp_path)
    _scan(vol)
    home = library.home()
    assert [row["key"] for row in home["rows"]] == ["recent", "films"]
    assert home["counts"]["films"] == 7 and home["hero"]
