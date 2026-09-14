import pytest

from aura.services.mediaparse import film_title, parse_path

FILMS = [
    ("Films/Le.Grand.Bleu.1988.MULTI.1080p.BluRay.x264-FGT.mkv", "Le Grand Bleu", "1988", "1080p", ("MULTI",)),
    ("Inception (2010)/movie.mkv", "Inception", "2010", "", ()),
    ("Blade.Runner.2049.2017.2160p.UHD.BluRay.mkv", "Blade Runner 2049", "2017", "4K", ()),
    ("1917.2019.MULTI.mkv", "1917", "2019", "", ("MULTI",)),
    ("Downloads/www.Torrent9.ph - Intouchables.2011.FRENCH.720p.mkv", "Intouchables", "2011", "720p", ("VF",)),
    ("Films/Mr. Nobody (2009).mkv", "Mr. Nobody", "2009", "", ()),
    ("Films/The French Dispatch (2021) VOSTFR.mkv", "The French Dispatch", "2021", "", ("VOSTFR",)),
    ("Movies/Interstellar.MULTi.1080p.WEB.x265.mkv", "Interstellar", "", "1080p", ("MULTI",)),
    ("Films/Heat (1995)/Heat.mkv", "Heat", "1995", "", ()),
    ("Films/Amelie (2001)/CD1.avi", "Amelie", "2001", "", ()),
]

EPISODES = [
    ("Séries/Breaking Bad/Saison 2/05 - Breakage.mkv", "Breaking Bad", 2, 5, "Breakage"),
    ("Breaking.Bad.S01E01.Pilot.720p.WEB-DL.mkv", "Breaking Bad", 1, 1, "Pilot"),
    ("Friends.1x05.avi", "Friends", 1, 5, ""),
    ("Séries/Chernobyl/03.mkv", "Chernobyl", 1, 3, ""),
    ("Dark/Season 1/S01E02 - Lies.mkv", "Dark", 1, 2, "Lies"),
    ("the wire s01e01.mkv", "The Wire", 1, 1, ""),
    ("S.W.A.T.2017.S01E01.mkv", "SWAT", 1, 1, ""),
    ("Séries/Game.of.Thrones.S03.1080p/Game.of.Thrones.S03E09.mkv", "Game of Thrones", 3, 9, ""),
    ("Séries/Lupin S02/Episode 4.mkv", "Lupin", 2, 4, ""),
]


@pytest.mark.parametrize("path,title,year,quality,langs", FILMS)
def test_films(path, title, year, quality, langs):
    parsed = parse_path(path)
    assert (parsed.kind, parsed.title, parsed.year, parsed.quality, parsed.langs) == ("film", title, year, quality, langs)


@pytest.mark.parametrize("path,show,season,episode,episode_title", EPISODES)
def test_episodes(path, show, season, episode, episode_title):
    parsed = parse_path(path)
    assert (parsed.kind, parsed.title, parsed.season, parsed.episode, parsed.episode_title) == ("episode", show, season, episode, episode_title)


def test_films_in_series_folders_without_numbers_stay_films():
    assert parse_path("Séries/Friends/Friends The Reunion (2021).mkv").kind == "film"


def test_titles_that_look_like_tags_survive():
    assert film_title("Charlotte's Web (2006)") == ("Charlotte's Web", "2006")
    assert film_title("300") == ("300", "")
