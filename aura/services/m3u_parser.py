"""Tolerant extended-M3U parser.

Handles:
  #EXTM3U url-tvg="..." x-tvg-url="..."
  #EXTINF:-1 tvg-id="..." tvg-name="..." tvg-logo="..." group-title="...",Name
  #EXTGRP:Group
  #EXTVLCOPT:http-user-agent=... / http-referrer=...
  #KODIPROP:key=value
Missing fields never raise; they default to ''.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from ..models import Kind, StreamItem

_ATTR = re.compile(r'([A-Za-z0-9_.\-]+)="([^"]*)"')
_ATTR_BARE = re.compile(r'([A-Za-z0-9_.\-]+)=([^\s",]+)')
_DURATION = re.compile(r"^-?\d+(?:\.\d+)?")


def split_extinf(line: str) -> tuple[str, str]:
    """Split '#EXTINF:-1 a="x, y" b="z",Name' into (attribute text, name).

    The name is whatever follows the LAST comma that sits outside double quotes, so commas inside
    attribute values (user-agent strings, titles) do not truncate the name.
    """
    body = line[len("#EXTINF:"):]
    body = _DURATION.sub("", body.lstrip(), count=1)
    in_quote = False
    last_comma = -1
    for i, ch in enumerate(body):
        if ch == '"':
            in_quote = not in_quote
        elif ch == "," and not in_quote:
            last_comma = i
    if last_comma == -1:
        return body.strip(), ""
    return body[:last_comma].strip(), body[last_comma + 1:].strip()

_URL_FORBIDDEN = frozenset(' <>"' + chr(39) + chr(9))


def is_stream_url(line: str) -> bool:
    """'scheme://target' with no spaces, quotes or angle brackets. HTML that merely contains a link is not a URL."""
    scheme, sep, rest = line.partition("://")
    return (
        bool(sep)
        and bool(rest)
        and scheme[:1].isalpha()
        and all(ch.isalnum() or ch in "+.-" for ch in scheme)
        and not any(ch in _URL_FORBIDDEN for ch in rest)
    )


VOD_EXT = (".mp4", ".mkv", ".avi", ".mov", ".m4v", ".webm", ".flv", ".wmv")


@dataclass(frozen=True)
class ParsedPlaylist:
    items: tuple[StreamItem, ...]
    epg_url: str = ""
    header: dict[str, str] = field(default_factory=dict)


def parse_attrs(text: str) -> dict[str, str]:
    attrs = {k.lower(): v for k, v in _ATTR.findall(text)}
    stripped = _ATTR.sub("", text)
    for k, v in _ATTR_BARE.findall(stripped):
        attrs.setdefault(k.lower(), v)
    return attrs


def guess_kind(url: str, group: str = "", explicit: str = "") -> Kind:
    """Live unless the URL clearly points to a file/VOD path. Group hints ('Films', 'Séries')
    only count when the URL is not an obvious live stream (.m3u8/.ts), because public playlists
    such as iptv-org use group-title='movies' for live movie channels."""
    if explicit in ("live", "vod", "series"):
        return explicit  # type: ignore[return-value]
    path = urlparse(url).path.lower()
    if "/series/" in path:
        return "series"
    if "/movie/" in path or path.endswith(VOD_EXT):
        return "vod"
    if path.endswith((".m3u8", ".m3u", ".ts")):
        return "live"
    g = (group or "").lower()
    if "serie" in g:
        return "series"
    if "vod" in g or "film" in g or "movie" in g:
        return "vod"
    return "live"


def parse_m3u(text: str, source_id: str = "") -> ParsedPlaylist:
    items: list[StreamItem] = []
    header: dict[str, str] = {}
    pending: dict[str, str] | None = None
    current_group = ""
    opts: dict[str, str] = {}

    for raw in text.splitlines():
        line = raw.strip().lstrip("﻿")
        if not line:
            continue
        upper = line.upper()
        if upper.startswith("#EXTM3U"):
            header = parse_attrs(line[7:])
            continue
        if upper.startswith("#EXTINF"):
            attr_text, name = split_extinf(line)
            attrs = parse_attrs(attr_text)
            pending = {**attrs, "_name": name or attrs.get("tvg-name", "")}
            continue
        if upper.startswith("#EXTGRP:"):
            current_group = line[8:].strip()
            continue
        if upper.startswith("#EXTVLCOPT:"):
            k, _, v = line[11:].partition("=")
            opts[k.strip().lower()] = v.strip()
            continue
        if upper.startswith("#KODIPROP:"):
            k, _, v = line[10:].partition("=")
            opts["kodiprop:" + k.strip().lower()] = v.strip()
            continue
        if line.startswith("#"):
            continue
        # URL line. Anything without a scheme is not a stream: an HTML page served instead of a
        # playlist, or a placeholder such as "[NO PUBLIC STREAM]". Keeping it creates fake channels.
        if not is_stream_url(line):
            pending = None
            opts = {}
            continue
        if pending is None:
            pending = {"_name": line.rsplit("/", 1)[-1] or line}
        group = pending.get("group-title") or current_group
        name = pending.get("_name") or pending.get("tvg-name") or line
        extra = dict(opts)
        for k in (
            "tvg-name",
            "tvg-language",
            "tvg-country",
            "tvg-chno",
            "catchup",
            "catchup-source",
            "catchup-days",
        ):
            if pending.get(k):
                extra[k] = pending[k]
        items.append(
            StreamItem.make(
                name=name,
                url=line,
                group=group,
                logo=pending.get("tvg-logo", ""),
                tvg_id=pending.get("tvg-id", ""),
                kind=guess_kind(line, group, pending.get("tvg-type", "")),
                source_id=source_id,
                extra=extra,
            )
        )
        pending = None
        opts = {}

    epg_url = header.get("url-tvg") or header.get("x-tvg-url") or ""
    return ParsedPlaylist(items=tuple(items), epg_url=epg_url.split(",")[0].strip(), header=header)


def to_m3u(items: list[StreamItem] | tuple[StreamItem, ...]) -> str:
    """Serialise items back to extended M3U (used for exports and debugging)."""
    out = ["#EXTM3U"]
    for it in items:
        out.append(
            f'#EXTINF:-1 tvg-id="{it.tvg_id}" tvg-logo="{it.logo}" group-title="{it.group}",{it.name}'
        )
        out.append(it.url)
    return "\n".join(out) + "\n"
