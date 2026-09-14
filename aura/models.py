"""Immutable domain models. All objects are frozen dataclasses; use dataclasses.replace to derive."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Kind = Literal["live", "vod", "series"]


def stable_id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8", "ignore")).hexdigest()[:16]


@dataclass(frozen=True)
class StreamItem:
    id: str
    name: str
    url: str
    group: str = ""
    logo: str = ""
    tvg_id: str = ""
    kind: Kind = "live"
    source_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def make(name: str, url: str, **kw: Any) -> "StreamItem":
        return StreamItem(id=stable_id(url, name), name=name.strip(), url=url.strip(), **kw)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EpgChannel:
    id: str
    display_name: str
    icon: str = ""


@dataclass(frozen=True)
class EpgProgramme:
    channel_id: str
    start: int  # unix seconds UTC
    stop: int
    title: str
    description: str = ""
    category: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SportsEvent:
    id: str
    sport: str  # f1 | ufc | boxing | other
    name: str
    session: str  # Race, Qualifying, Main Card, ...
    start: int  # unix seconds UTC (0 if unknown)
    end: int = 0
    location: str = ""
    url: str = ""
    round: str = ""
    keywords: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["keywords"] = list(self.keywords)
        return d


@dataclass(frozen=True)
class Source:
    id: str
    type: Literal["m3u_url", "m3u_file", "xtream", "free", "archive"]
    name: str
    url: str = ""
    username: str = ""
    password: str = ""
    epg_url: str = ""
    enabled: bool = True
    last_refresh: int = 0
    status: str = ""
    item_count: int = 0

    def to_public_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["password"] = "••••••" if self.password else ""
        return d
