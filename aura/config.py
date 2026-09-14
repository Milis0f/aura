"""Runtime configuration. Everything is derived from a few environment variables."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"

IS_LINUX = sys.platform.startswith("linux")


def _default_data_dir() -> Path:
    env = os.environ.get("AURA_DATA")
    if env:
        return Path(env)
    if IS_LINUX:
        return Path("/var/lib/aura")
    return PACKAGE_DIR.parent / "data"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    host: str
    port: int
    mpv_socket: str
    chrome_debug_port: int
    hotspot_ssid: str
    hotspot_password: str
    http_timeout: float
    epg_refresh_hours: int
    sources_refresh_hours: int
    sports_refresh_hours: int
    user_agent: str
    # NAS side (names kept from NAS Dashboard v2 so an existing /etc/nasdash.env keeps working)
    session_ttl: int
    idle_ttl: int
    max_fails: int
    lockout_seconds: int
    secure_cookie: str  # auto | 1 | 0
    qb_url: str
    qb_user: str
    qb_pass: str = field(repr=False)
    jellyfin_url: str = ""
    media_root: str = ""
    file_roots: tuple[tuple[str, str], ...] = ()
    library_dirs: tuple[str, ...] = ()
    manage_network: bool = True  # False on OpenMediaVault boxes: OMV owns the network configuration
    automount: bool = True

    @property
    def db_path(self) -> Path:
        return self.data_dir / "aura.db"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"


def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name)
        if value not in (None, ""):
            return value
    return default


def _flag(*names: str, default: bool = True) -> bool:
    return _env(*names, default="1" if default else "0").strip().lower() not in ("0", "false", "no", "off")


def parse_file_roots(raw: str) -> tuple[tuple[str, str], ...]:
    """'Media:/srv/disk/Media,Disque:/srv/disk' -> (('Media', '/srv/disk/Media'), ('Disque', '/srv/disk'))."""
    roots = []
    for entry in raw.split(","):
        label, sep, path = entry.strip().partition(":")
        if sep and label.strip() and path.strip():
            roots.append((label.strip(), path.strip()))
    return tuple(roots)


def load_settings() -> Settings:
    data_dir = _default_data_dir()
    return Settings(
        data_dir=data_dir,
        host=os.environ.get("AURA_HOST", "0.0.0.0"),
        # 8000: the NAS dashboard's port. 8080 belongs to qBittorrent on the same machine.
        port=int(os.environ.get("AURA_PORT") or os.environ.get("PORT") or "8000"),
        mpv_socket=os.environ.get("AURA_MPV_SOCKET", "/run/aura/mpv.sock"),
        chrome_debug_port=int(os.environ.get("AURA_CHROME_PORT", "9222")),
        hotspot_ssid=os.environ.get("AURA_HOTSPOT_SSID", "Aura-Setup"),
        hotspot_password=os.environ.get("AURA_HOTSPOT_PASSWORD", "aura123"),
        http_timeout=float(os.environ.get("AURA_HTTP_TIMEOUT", "30")),
        epg_refresh_hours=int(os.environ.get("AURA_EPG_REFRESH_HOURS", "6")),
        sources_refresh_hours=int(os.environ.get("AURA_SOURCES_REFRESH_HOURS", "12")),
        sports_refresh_hours=int(os.environ.get("AURA_SPORTS_REFRESH_HOURS", "6")),
        user_agent=os.environ.get(
            "AURA_USER_AGENT",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0 Safari/537.36 Aura/0.1",
        ),
        session_ttl=int(_env("AURA_SESSION_TTL", "SESSION_TTL", default="43200")),
        idle_ttl=int(_env("AURA_IDLE_TTL", "IDLE_TTL", default="86400")),
        max_fails=int(_env("AURA_MAX_FAILS", "MAX_FAILS", default="8")),
        lockout_seconds=int(_env("AURA_LOCKOUT_SECONDS", "LOCKOUT_SECONDS", default="600")),
        secure_cookie=_env("AURA_SECURE_COOKIE", "SECURE_COOKIE", default="auto").lower(),
        qb_url=_env("AURA_QB_URL", "QB_URL", default="http://127.0.0.1:8080").rstrip("/"),
        qb_user=_env("AURA_QB_USER", "QB_USER"),
        qb_pass=_env("AURA_QB_PASS", "QB_PASS"),
        jellyfin_url=_env("AURA_JELLYFIN_URL", "JELLYFIN_URL"),
        media_root=_env("AURA_MEDIA_ROOT", "MEDIA_ROOT"),
        file_roots=parse_file_roots(_env("AURA_FILE_ROOTS", "FILE_ROOTS")),
        library_dirs=tuple(p for p in _env("AURA_LIBRARY_DIRS").split(os.pathsep) if p.strip()),
        manage_network=_flag("AURA_MANAGE_NETWORK"),
        automount=_flag("AURA_AUTOMOUNT"),
    )


SETTINGS = load_settings()

# Free, public playlists bundled as "one-click" sources (iptv-org, community maintained).
FREE_BUNDLES: tuple[dict[str, str], ...] = (
    {"key": "archive-films", "name": "Films classiques libres de droits (Internet Archive)", "url": "archive://feature_films", "type": "archive"},
    {"key": "fr", "name": "Chaînes France", "url": "https://iptv-org.github.io/iptv/countries/fr.m3u"},
    {"key": "sports", "name": "Sports monde", "url": "https://iptv-org.github.io/iptv/categories/sports.m3u"},
    {"key": "news", "name": "Infos monde", "url": "https://iptv-org.github.io/iptv/categories/news.m3u"},
    {"key": "movies", "name": "Chaînes cinéma", "url": "https://iptv-org.github.io/iptv/categories/movies.m3u"},
    {"key": "series", "name": "Chaînes séries", "url": "https://iptv-org.github.io/iptv/categories/series.m3u"},
    {"key": "documentary", "name": "Documentaires", "url": "https://iptv-org.github.io/iptv/categories/documentary.m3u"},
    {"key": "music", "name": "Musique", "url": "https://iptv-org.github.io/iptv/categories/music.m3u"},
    {"key": "kids", "name": "Jeunesse", "url": "https://iptv-org.github.io/iptv/categories/kids.m3u"},
    {"key": "24-7", "name": "Chaînes 24/7 (séries et films en boucle)", "url": "https://iptv-org.github.io/iptv/index.category.m3u"},
    {"key": "be", "name": "Chaînes Belgique", "url": "https://iptv-org.github.io/iptv/countries/be.m3u"},
    {"key": "ch", "name": "Chaînes Suisse", "url": "https://iptv-org.github.io/iptv/countries/ch.m3u"},
    {"key": "ca", "name": "Chaînes Canada", "url": "https://iptv-org.github.io/iptv/countries/ca.m3u"},
    {"key": "uk", "name": "Chaînes Royaume-Uni", "url": "https://iptv-org.github.io/iptv/countries/uk.m3u"},
    {"key": "us", "name": "Chaînes USA", "url": "https://iptv-org.github.io/iptv/countries/us.m3u"},
    {"key": "all", "name": "Tout le catalogue public (11 000 chaînes, lent)", "url": "https://iptv-org.github.io/iptv/index.m3u"},
)

# Free EPG sources (XMLTV). First match wins, the rest fill the gaps.
FREE_EPG: tuple[dict[str, str], ...] = (
    {"key": "epgshare-fr", "name": "EPGShare France (recommandé, .gz)", "url": "https://epgshare01.online/epgshare01/epg_ripper_FR1.xml.gz"},
    {"key": "epgpw-fr", "name": "epg.pw France", "url": "https://epg.pw/xmltv/epg_FR.xml"},
    {"key": "openepg-fr", "name": "open-epg France", "url": "https://www.open-epg.com/files/france1.xml"},
    {"key": "epgshare-uk", "name": "EPGShare UK (.gz)", "url": "https://epgshare01.online/epgshare01/epg_ripper_UK1.xml.gz"},
    {"key": "epgpw-gb", "name": "epg.pw Royaume-Uni", "url": "https://epg.pw/xmltv/epg_GB.xml"},
    {"key": "epgshare-us", "name": "EPGShare USA (.gz)", "url": "https://epgshare01.online/epgshare01/epg_ripper_US1.xml.gz"},
)

# DRM / web apps launched inside the kiosk browser (Google Chrome with Widevine).
WEB_APPS: tuple[dict[str, str], ...] = (
    {"key": "netflix", "name": "Netflix", "url": "https://www.netflix.com/browse", "color": "#e50914"},
    {"key": "prime", "name": "Prime Video", "url": "https://www.primevideo.com/", "color": "#1a98ff"},
    {"key": "canal", "name": "Canal+", "url": "https://www.canalplus.com/", "color": "#0a0a0a"},
    {"key": "disney", "name": "Disney+", "url": "https://www.disneyplus.com/", "color": "#0a2a6b"},
    {"key": "youtube", "name": "YouTube", "url": "https://www.youtube.com/tv", "color": "#ff0000"},
    {"key": "molotov", "name": "Molotov", "url": "https://www.molotov.tv/", "color": "#ff3b30"},
    {"key": "francetv", "name": "france.tv", "url": "https://www.france.tv/", "color": "#1f1f1f"},
    {"key": "tf1", "name": "TF1+", "url": "https://www.tf1.fr/", "color": "#0033a0"},
    {"key": "arte", "name": "ARTE", "url": "https://www.arte.tv/fr/", "color": "#fa481c"},
    {"key": "f1tv", "name": "F1 TV", "url": "https://f1tv.formula1.com/", "color": "#e10600"},
    {"key": "dazn", "name": "DAZN", "url": "https://www.dazn.com/", "color": "#f8f8f8"},
    {"key": "twitch", "name": "Twitch", "url": "https://www.twitch.tv/", "color": "#9146ff"},
)
