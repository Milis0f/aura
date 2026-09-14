"""Turn a video path into a film title and year, or a series with a season and an episode.

Release names are noisy ("Le.Grand.Bleu.1988.MULTI.1080p.BluRay.x264-FGT.mkv") and folders often say more than
files ("Inception (2010)/movie.mkv", "Breaking Bad/Saison 2/05 - Breakage.mkv"). The parser reads the file name
first and falls back to its folders, which is how people organise a drive by hand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from .textutil import fold

VIDEO_EXTENSIONS = frozenset({
    ".mkv", ".mp4", ".m4v", ".avi", ".mov", ".webm", ".ts", ".m2ts", ".mts", ".wmv", ".mpg", ".mpeg",
    ".flv", ".ogv", ".divx", ".3gp",
})

# Folders that group content without naming it.
CONTAINER_DIRS = frozenset({
    "films", "film", "movies", "movie", "media", "medias", "médias", "videos", "vidéos", "video", "vidéo",
    "downloads", "download", "téléchargements", "telechargements", "completed", "complete", "torrents",
    "series", "séries", "serie", "série", "tv", "tv shows", "shows", "anime", "animes", "mangas", "manga",
    "dessins animés", "dessins animes", "documentaires", "docs", "kids", "enfants", "4k", "hd", "uhd",
})
SERIES_DIRS = frozenset({
    "series", "séries", "serie", "série", "tv", "tv shows", "shows", "anime", "animes", "mangas", "manga",
    "dessins animés", "dessins animes",
})
GENERIC_STEMS = frozenset({
    "movie", "film", "video", "vidéo", "sample", "title", "track", "main", "feature", "episode", "épisode",
    "vts", "bdmv", "stream", "index", "cd1", "cd2", "disc1", "disc2", "part1", "part2",
})
_SMALL_WORDS = frozenset({
    "de", "du", "des", "la", "le", "les", "et", "à", "au", "aux", "en", "un", "une", "d", "l",
    "of", "the", "and", "a", "an", "in", "on", "to", "for",
})

_SXE = re.compile(r"(?i)(?<![a-z0-9])s(\d{1,2})[ ._-]?e(\d{1,3})(?:[ ._-]?e(\d{1,3}))?(?!\d)")
_NXN = re.compile(r"(?i)(?<![0-9a-z])(\d{1,2})x(\d{2,3})(?![0-9])")
_SAISON_EP = re.compile(r"(?i)(?<![a-z])(?:saison|season)[ ._-]*(\d{1,2})[ ._,-]*(?:episode|épisode|ep\.?)[ ._-]*(\d{1,3})(?!\d)")
_SEASON_DIR = re.compile(r"(?i)^\s*(?:season|saison|staffel|temporada|series|s)[ ._-]*(\d{1,2})(?:\b.*)?$")
_SEASON_IN_NAME = re.compile(r"(?i)(?<![a-z0-9])(?:s|saison[ ._-]*|season[ ._-]*)(\d{1,2})(?![0-9]|e\d)")
_EPISODE_ONLY = re.compile(r"(?i)(?<![a-z0-9])(?:e|ep|episode|épisode)[ ._-]*(\d{1,3})(?!\d)")
_LEADING_NUMBER = re.compile(r"^\s*(\d{1,3})(?:\s*[-._)]\s*|\s+|$)")
_YEAR_PAREN = re.compile(r"[(\[]\s*((?:19|20)\d{2})\s*[)\]]")
_YEAR = re.compile(r"(?<![0-9])((?:19\d|20[0-4])\d)(?![0-9])")
_BRACKETS = re.compile(r"\[[^\]]*\]|\{[^}]*\}")
_SITE = re.compile(
    r"(?i)^\s*(?:www\.)?[a-z0-9-]+\.(?:com|net|org|to|ws|cc|tv|ph|fr|xyz|io|me|click|lol|site|vip|nz|re|biz|info)\b\s*[-–_. ]*\s*"
)
_LANG_PREFIX = re.compile(r"^\s*(?:FR|VF|VOSTFR|MULTI|EN)\s*[-|:]\s*")
_ACRONYM = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z]\.){2,}[A-Za-z]?(?![A-Za-z0-9])")

# Tags that never belong to a title. "FRENCH" only counts in capitals: "The French Dispatch" is a title.
_STRONG = (
    r"2160p|1440p|1080p|1080i|720p|576p|480p|4k|uhd|hdr10\+?|hdr|dolby[ ._-]?vision|10bits?|x264|x265|"
    r"h[ ._]?264|h[ ._]?265|hevc|avc|av1|xvid|divx|blu[ ._-]?ray|bdrip|brrip|bd[ ._-]?remux|remux|web[ ._-]?dl|"
    r"webrip|hdtv|hdrip|dvdrip|dvdscr|hdlight|mhd|multi|truefrench|vff|vfq|vfi|vf2|vostfr|subfrench|aac|ac3|"
    r"e-?ac3|dts(?:[ ._-]?hd)?|truehd|atmos|ddp?[ ._]?[257][ ._]1"
)
_WEAK = r"web|vf|vo|vost|dv|nf|amzn|dsnp|hmax|atvp|proper|repack|internal|limited|extended|unrated|uncut|remastered|imax|complete|integrale|intégrale|hd|sd|fhd"
_STRONG_TAG = re.compile(rf"(?:(?i:(?<![a-z0-9])(?:{_STRONG})(?![a-z0-9]))|(?<![A-Za-z0-9])FRENCH(?![A-Za-z0-9]))")
_STRONG_TRAIL = re.compile(rf"(?:[ ._-]+(?:(?i:{_STRONG})|FRENCH))+[ ._-]*$")
_WEAK_TRAIL = re.compile(rf"(?i)(?:[ ._-]+(?:{_WEAK}|{_STRONG}))+[ ._-]*$")
_GENERIC_NAME = re.compile(r"(?i)^(?:vts[ _]\d+[ _]\d+|title\s*\d+|t\d{2}|disc\s*\d+|cd\s*\d+|part\s*\d+|\d{1,2})$")


@dataclass(frozen=True)
class ParsedName:
    kind: str  # "film" | "episode"
    title: str
    year: str = ""
    season: int = 0
    episode: int = 0
    episode_title: str = ""
    quality: str = ""  # "4K" | "1080p" | "720p" | "SD" | ""
    langs: tuple[str, ...] = ()  # "MULTI", "VF", "VOSTFR"


def is_video(name: str) -> bool:
    return PurePosixPath(name).suffix.lower() in VIDEO_EXTENSIONS


def quality_of(text: str) -> str:
    t = text.lower()
    if re.search(r"(?<![a-z0-9])(?:2160p|4k|uhd)(?![a-z0-9])", t):
        return "4K"
    if re.search(r"(?<![a-z0-9])1080[pi](?![a-z0-9])", t):
        return "1080p"
    if re.search(r"(?<![a-z0-9])720p(?![a-z0-9])", t):
        return "720p"
    if re.search(r"(?<![a-z0-9])(?:576p|480p|dvdrip|xvid)(?![a-z0-9])", t):
        return "SD"
    return ""


def langs_of(text: str) -> tuple[str, ...]:
    found: list[str] = []
    if re.search(r"(?i)(?<![a-z0-9])multi(?![a-z0-9])", text):
        found.append("MULTI")
    if re.search(r"(?i)(?<![a-z0-9])(?:truefrench|vff|vfq|vfi|vf2?)(?![a-z0-9])", text) or re.search(r"(?<![A-Za-z0-9])FRENCH(?![A-Za-z0-9])", text):
        found.append("VF")
    if re.search(r"(?i)(?<![a-z0-9])(?:vostfr|subfrench|vost)(?![a-z0-9])", text):
        found.append("VOSTFR")
    return tuple(found)


def _is_release(text: str) -> bool:
    stem = text.strip()
    return " " not in stem and (stem.count(".") >= 2 or "_" in stem)


def _smart_case(text: str) -> str:
    letters = [c for c in text if c.isalpha()]
    if not letters or not (text == text.lower() or text == text.upper()):
        return text
    if text == text.upper() and " " not in text and len(letters) <= 5:
        return text  # an acronym such as "SWAT" or "MASH"
    words = text.lower().split(" ")
    return " ".join(w if (i and w in _SMALL_WORDS) else w[:1].upper() + w[1:] for i, w in enumerate(words))


def _tidy(text: str, release: bool) -> str:
    t = _ACRONYM.sub(lambda m: m.group(0).replace(".", ""), text)
    if release:
        t = t.replace(".", " ").replace("_", " ")
    t = re.sub(r"\(\s*\)", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -–_.,:;|")
    return _smart_case(t)


def film_title(raw: str) -> tuple[str, str]:
    """'Le.Grand.Bleu.1988.MULTI.1080p' -> ('Le Grand Bleu', '1988'); 'Mr. Nobody (2009)' -> ('Mr. Nobody', '2009')."""
    t = raw.strip()
    release = _is_release(t)
    while True:
        stripped = _SITE.sub("", t, count=1)
        if stripped == t:
            break
        t = stripped
    t = _BRACKETS.sub(" ", t)
    year = ""
    m = _YEAR_PAREN.search(t)
    if m:
        year = m.group(1)
        head = t[: m.start()]
        t = head if head.strip(" ._-") else t[m.end():]
        t = _STRONG_TRAIL.sub("", t)
    else:
        years = [y for y in _YEAR.finditer(t) if y.start() > 0]
        if years:
            year = years[-1].group(1)
            t = _STRONG_TRAIL.sub("", t[: years[-1].start()])
        else:
            tag = _STRONG_TAG.search(t)
            if tag and tag.start() > 0:
                t = t[: tag.start()]
                if release:
                    t = _WEAK_TRAIL.sub("", t)
            elif release:
                t = re.sub(r"-[A-Za-z0-9]{2,12}$", "", t)
    t = _LANG_PREFIX.sub("", t)
    return _tidy(t, release), year


def _is_generic(title: str) -> bool:
    return len(title) < 2 or fold(title) in GENERIC_STEMS or bool(_GENERIC_NAME.match(title.strip()))


def _episode_title(rest: str) -> str:
    tag = _STRONG_TAG.search(rest)
    text = rest[: tag.start()] if tag else rest
    text = _WEAK_TRAIL.sub("", " " + text)
    tidy = _tidy(text, _is_release(rest) or "." in rest)
    return "" if _is_generic(tidy) and not tidy.isdigit() else tidy


def _episode_in_stem(stem: str) -> tuple[str, int, int, str] | None:
    for rx in (_SXE, _SAISON_EP, _NXN):
        m = rx.search(stem)
        if m:
            return stem[: m.start()], int(m.group(1)), int(m.group(2)), stem[m.end():]
    return None


def _show_from_folders(parents: list[str]) -> str:
    for folder in reversed(parents):
        if folder.lower() in CONTAINER_DIRS or _SEASON_DIR.match(folder):
            continue
        cut = _SEASON_IN_NAME.search(folder)
        title, _ = film_title(folder[: cut.start()] if cut and cut.start() > 0 else folder)
        if title and not _is_generic(title):
            return title
    return ""


def _episode_in_folders(stem: str, parents: list[str], series_context: bool) -> tuple[str, int, int, str] | None:
    season: int | None = None
    show_raw = ""
    if parents:
        last = parents[-1]
        m = _SEASON_DIR.match(last)
        if m:
            season = int(m.group(1))
        else:
            m2 = _SEASON_IN_NAME.search(last)
            if m2 and m2.start() > 0 and not _SXE.search(last):
                season = int(m2.group(1))
                show_raw = last[: m2.start()]
    if season is None and not series_context:
        return None
    e = _EPISODE_ONLY.search(stem) or _LEADING_NUMBER.match(stem)
    if not e:
        return None
    return show_raw, season if season is not None else 1, int(e.group(1)), stem[e.end():]


def parse_path(rel_path: str) -> ParsedName:
    p = PurePosixPath(rel_path.replace("\\", "/"))
    stem = p.stem
    parents = [d for d in p.parent.parts if d not in ("", ".", "/")]
    context = " ".join([*parents[-2:], p.name])
    quality, langs = quality_of(context), langs_of(context)
    series_context = any(d.lower() in SERIES_DIRS for d in parents)

    found = _episode_in_stem(stem) or _episode_in_folders(stem, parents, series_context)
    if found:
        show_raw, season, episode, rest = found
        show = film_title(show_raw)[0] if show_raw.strip(" ._-") else ""
        if not show or _is_generic(show):
            show = _show_from_folders(parents)
        if show:
            return ParsedName("episode", show, "", season, episode, _episode_title(rest), quality, langs)

    title, year = film_title(stem)
    if not title or _is_generic(title):
        for folder in reversed(parents):
            if folder.lower() in CONTAINER_DIRS:
                continue
            folder_title, folder_year = film_title(folder)
            if folder_title and not _is_generic(folder_title):
                title, year = folder_title, year or folder_year
                break
    elif not year and parents:
        folder_title, folder_year = film_title(parents[-1])
        if folder_year and fold(folder_title) == fold(title):
            year = folder_year
    return ParsedName("film", title or stem, year, 0, 0, "", quality, langs)
