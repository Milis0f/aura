"""Xtream Codes API client (player_api.php) and URL builders.

Endpoints (all GET, auth via username/password query params):
  player_api.php                         -> user_info + server_info
  ?action=get_live_categories / get_vod_categories / get_series_categories
  ?action=get_live_streams[&category_id]
  ?action=get_vod_streams[&category_id]
  ?action=get_series[&category_id]
  ?action=get_series_info&series_id=X
  ?action=get_vod_info&vod_id=X
  ?action=get_short_epg&stream_id=X&limit=N
  xmltv.php?username&password             -> XMLTV EPG
Stream URLs:
  {base}/live/{user}/{pass}/{stream_id}.{m3u8|ts}
  {base}/movie/{user}/{pass}/{stream_id}.{container_extension}
  {base}/series/{user}/{pass}/{episode_id}.{container_extension}
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from ..models import StreamItem

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class XtreamCreds:
    base_url: str
    username: str
    password: str

    @staticmethod
    def from_any(url: str, username: str, password: str) -> "XtreamCreds":
        """Accept 'http://host:port', 'http://host:port/get.php?username=..' or a bare host."""
        u = url.strip()
        if "://" not in u:
            u = "http://" + u
        p = urlparse(u)
        base = f"{p.scheme}://{p.netloc}"
        return XtreamCreds(base_url=base, username=username.strip(), password=password.strip())

    @property
    def api(self) -> str:
        return (
            f"{self.base_url}/player_api.php?username={quote(self.username)}"
            f"&password={quote(self.password)}"
        )

    @property
    def epg_url(self) -> str:
        return (
            f"{self.base_url}/xmltv.php?username={quote(self.username)}"
            f"&password={quote(self.password)}"
        )

    @property
    def m3u_url(self) -> str:
        return (
            f"{self.base_url}/get.php?username={quote(self.username)}"
            f"&password={quote(self.password)}&type=m3u_plus&output=m3u8"
        )


def live_url(c: XtreamCreds, stream_id: int | str, ext: str = "m3u8") -> str:
    return f"{c.base_url}/live/{quote(c.username)}/{quote(c.password)}/{stream_id}.{ext}"


def vod_url(c: XtreamCreds, stream_id: int | str, ext: str = "mp4") -> str:
    return f"{c.base_url}/movie/{quote(c.username)}/{quote(c.password)}/{stream_id}.{ext or 'mp4'}"


def series_url(c: XtreamCreds, episode_id: int | str, ext: str = "mp4") -> str:
    return f"{c.base_url}/series/{quote(c.username)}/{quote(c.password)}/{episode_id}.{ext or 'mp4'}"


def pick_live_ext(user_info: dict[str, Any]) -> str:
    fmts = [str(f).lower() for f in (user_info or {}).get("allowed_output_formats") or []]
    if "m3u8" in fmts:
        return "m3u8"
    if "ts" in fmts:
        return "ts"
    return "m3u8"


class XtreamClient:
    def __init__(self, creds: XtreamCreds, http: httpx.AsyncClient):
        self.c = creds
        self.http = http

    async def _get(self, **params: Any) -> Any:
        clean = {k: v for k, v in params.items() if v is not None}
        r = await self.http.get(self.c.api, params=clean)
        r.raise_for_status()
        return r.json()

    async def auth(self) -> dict[str, Any]:
        data = await self._get()
        if not isinstance(data, dict) or not data.get("user_info"):
            raise ValueError("Réponse Xtream invalide (pas de user_info)")
        if str(data["user_info"].get("auth", "1")) in ("0", "False", "false"):
            raise ValueError("Identifiants Xtream refusés")
        return data

    async def categories(self, kind: str) -> dict[str, str]:
        action = {
            "live": "get_live_categories",
            "vod": "get_vod_categories",
            "series": "get_series_categories",
        }[kind]
        data = await self._get(action=action)
        return {
            str(c.get("category_id")): str(c.get("category_name", ""))
            for c in (data or [])
            if isinstance(c, dict)
        }

    async def live_streams(self) -> list[dict[str, Any]]:
        return list(await self._get(action="get_live_streams") or [])

    async def vod_streams(self) -> list[dict[str, Any]]:
        return list(await self._get(action="get_vod_streams") or [])

    async def series(self) -> list[dict[str, Any]]:
        return list(await self._get(action="get_series") or [])

    async def series_info(self, series_id: int | str) -> dict[str, Any]:
        return dict(await self._get(action="get_series_info", series_id=series_id) or {})

    async def vod_info(self, vod_id: int | str) -> dict[str, Any]:
        return dict(await self._get(action="get_vod_info", vod_id=vod_id) or {})

    async def short_epg(self, stream_id: int | str, limit: int = 4) -> list[dict[str, Any]]:
        data = await self._get(action="get_short_epg", stream_id=stream_id, limit=limit)
        return list((data or {}).get("epg_listings") or [])


def live_to_items(
    c: XtreamCreds,
    streams: list[dict[str, Any]],
    cats: dict[str, str],
    ext: str,
    source_id: str,
) -> list[StreamItem]:
    out = []
    for s in streams:
        sid = s.get("stream_id")
        if sid is None:
            continue
        out.append(
            StreamItem.make(
                name=str(s.get("name", "")),
                url=live_url(c, sid, ext),
                group=cats.get(str(s.get("category_id")), "") or "Live",
                logo=str(s.get("stream_icon") or ""),
                tvg_id=str(s.get("epg_channel_id") or ""),
                kind="live",
                source_id=source_id,
                extra={
                    "xtream_id": sid,
                    "num": s.get("num"),
                    "catchup": str(s.get("tv_archive") or "0"),
                },
            )
        )
    return out


def vod_to_items(
    c: XtreamCreds, streams: list[dict[str, Any]], cats: dict[str, str], source_id: str
) -> list[StreamItem]:
    out = []
    for s in streams:
        sid = s.get("stream_id")
        if sid is None:
            continue
        out.append(
            StreamItem.make(
                name=str(s.get("name", "")),
                url=vod_url(c, sid, str(s.get("container_extension") or "mp4")),
                group=cats.get(str(s.get("category_id")), "") or "Films",
                logo=str(s.get("stream_icon") or ""),
                kind="vod",
                source_id=source_id,
                extra={
                    "xtream_id": sid,
                    "rating": s.get("rating"),
                    "year": str(s.get("year") or ""),
                    "tmdb_id": s.get("tmdb_id") or s.get("tmdb"),
                    "added": s.get("added"),
                    "plot": s.get("plot") or "",
                },
            )
        )
    return out


def series_to_items(
    c: XtreamCreds, series: list[dict[str, Any]], cats: dict[str, str], source_id: str
) -> list[StreamItem]:
    """Series are stored as pseudo-items whose url is 'xtream-series://<src>/<id>', resolved lazily."""
    out = []
    for s in series:
        sid = s.get("series_id")
        if sid is None:
            continue
        out.append(
            StreamItem.make(
                name=str(s.get("name", "")),
                url=f"xtream-series://{source_id}/{sid}",
                group=cats.get(str(s.get("category_id")), "") or "Séries",
                logo=str(s.get("cover") or ""),
                kind="series",
                source_id=source_id,
                extra={
                    "xtream_id": sid,
                    "rating": s.get("rating"),
                    "year": str(s.get("releaseDate") or s.get("release_date") or "")[:4],
                    "plot": s.get("plot") or "",
                    "genre": s.get("genre") or "",
                },
            )
        )
    return out


def episodes_from_info(c: XtreamCreds, info: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten get_series_info into [{season, episode, title, url, plot, duration, image}]."""
    out: list[dict[str, Any]] = []
    eps = info.get("episodes") or {}
    seasons = eps.items() if isinstance(eps, dict) else enumerate(eps, 1)
    for season, lst in seasons:
        for e in lst or []:
            eid = e.get("id")
            if eid is None:
                continue
            ext = str(e.get("container_extension") or "mp4")
            meta = e.get("info") or {}
            out.append(
                {
                    "season": int(str(season)) if str(season).isdigit() else season,
                    "episode": e.get("episode_num"),
                    "title": e.get("title") or f"Épisode {e.get('episode_num')}",
                    "url": series_url(c, eid, ext),
                    "plot": meta.get("plot") or "",
                    "duration": meta.get("duration") or "",
                    "image": meta.get("movie_image") or "",
                }
            )
    return out


def _listify(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [x.strip() for x in str(v or "").split(",") if x.strip()]


def _minutes(v: Any) -> int:
    """'01:45:00' / '105 min' / 6300 (secs) -> minutes."""
    s = str(v or "").strip()
    if not s:
        return 0
    if ":" in s:
        parts = [int(x) for x in s.split(":") if x.isdigit()]
        if len(parts) == 3:
            return parts[0] * 60 + parts[1]
        if len(parts) == 2:
            return parts[0]
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return 0
    n = int(digits)
    return n // 60 if n > 600 else n


def info_to_meta(info: dict[str, Any]) -> dict[str, Any]:
    """Normalise get_vod_info / get_series_info 'info' blocks into the shared metadata shape."""
    if not info:
        return {}
    backdrops = info.get("backdrop_path") or []
    if isinstance(backdrops, str):
        backdrops = [backdrops]
    trailer = str(info.get("youtube_trailer") or "").strip()
    if trailer and not trailer.startswith("http"):
        trailer = f"https://www.youtube.com/watch?v={trailer}"
    return {
        "title": str(info.get("name") or info.get("title") or "").strip(),
        "year": str(info.get("releasedate") or info.get("releaseDate") or info.get("release_date") or "")[:4],
        "poster": str(info.get("movie_image") or info.get("cover_big") or info.get("cover") or ""),
        "backdrop": str(backdrops[0]) if backdrops else "",
        "overview": str(info.get("plot") or info.get("description") or "").strip(),
        "rating": info.get("rating") or info.get("rating_5based"),
        "genres": _listify(info.get("genre")),
        "runtime": _minutes(info.get("duration") or info.get("episode_run_time")),
        "trailer": trailer,
        "cast": _listify(info.get("cast") or info.get("actors"))[:10],
        "director": str(info.get("director") or "").strip(),
        "tmdb_id": info.get("tmdb_id") or info.get("tmdb"),
    }
