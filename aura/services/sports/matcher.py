"""Map a sports event to playable streams from the user's channels.

Strategy (scored, best first):
  1. EPG programme at event time whose title contains the event keywords -> that channel (+3)
  2. Channel name contains a sport keyword (Canal+ Sport, RMC Sport, DAZN, ...) (+2)
  3. Channel group contains 'sport' (+1)
Only the top N are returned so the UI can offer a failover list.
"""

from __future__ import annotations

from typing import Any

from .. import epg
from ...models import SportsEvent
from ..textutil import contains_any

SPORT_GROUP_HINTS = ("sport", "sports", "f1", "ufc", "mma", "boxe", "boxing", "fight")


def score_channel(ch: dict[str, Any], ev: SportsEvent, epg_hit_ids: set[str]) -> int:
    score = 0
    epg_id = ch.get("epg_id") or ch.get("tvg_id") or ""
    if epg_id and epg_id in epg_hit_ids:
        score += 3
    if contains_any(ch.get("name", ""), ev.keywords):
        score += 2
    if contains_any(ch.get("group_name", ""), SPORT_GROUP_HINTS):
        score += 1
    if ch.get("kind") != "live":
        score -= 5
    return score


def candidate_streams(ev: SportsEvent, channels: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    window_start = ev.start - 1800 if ev.start else 0
    window_end = (ev.end or ev.start + 3 * 3600) if ev.start else 0
    hits = (
        epg.search_programmes(list(ev.keywords[:6]), window_start, window_end)
        if ev.start
        else []
    )
    hit_ids = {h["channel_id"] for h in hits}
    scored = [(score_channel(c, ev, hit_ids), c) for c in channels]
    ranked = sorted(((s, c) for s, c in scored if s > 0), key=lambda t: t[0], reverse=True)
    return [{**c, "score": s} for s, c in ranked[:limit]]
