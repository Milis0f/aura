"""Text normalisation shared by parsers, matching and search.

Two different keys, on purpose:

- ``fold(text)`` is for search. It only removes accents, symbols and case, keeps every script and every
  word: searching "france" must find "France 24", and "че" must find a Cyrillic channel.
- ``normalize(text)`` is channel identity, used to pair the same channel across playlists (failover) and
  with the TV guide. It drops what describes the stream rather than the channel: quality tags, resolution,
  iptv-org status tags, a leading language prefix, trailing country codes. It never drops a word that
  starts the name, so "France 2" stays "france2" instead of collapsing to "2".
"""

from __future__ import annotations

import re
import unicodedata

# Tokens describing the stream, not the channel: "TF1 HD" and "TF1 FHD" are the same channel.
_QUALITY = frozenset({
    "hd", "fhd", "uhd", "sd", "hq", "lq", "4k", "8k", "hdr", "hevc", "hevh", "h264", "h265",
    "x264", "x265", "50fps", "60fps", "vip", "backup",
})
# Country / language qualifiers, only removed when they trail the name ("TF1 FR").
_QUALIFIERS = frozenset({"fr", "fra", "fre", "uk", "gb", "us", "usa", "de", "vf", "vo", "vost", "vostfr", "multi"})
_RESOLUTION = re.compile(r"^\d{3,4}[pi]$")
_STATUS_TAG = re.compile(r"\[[^\]]*\]")  # iptv-org: [Geo-blocked], [Not 24/7]
_RESOLUTION_PAREN = re.compile(r"\(\s*(?:\d{3,4}[pi]|hd|fhd|uhd|sd|4k)\s*\)", re.I)
_LANG_PREFIX = re.compile(r"^\s*(?:FR|BE|CH|CA|UK|US|DE|ES|IT|PT|NL|AR|TR|VF|VO|VOST|VOSTFR|MULTI)\s*[:|\-]\s*")
_WORD = re.compile(r"[^\W_]+")


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _without_symbols(text: str) -> str:
    # Symbols go before NFKD: a circled letter such as "Ⓨ" would otherwise decompose into a plain "Y".
    return "".join(ch for ch in text if not unicodedata.category(ch).startswith("S"))


def fold(text: str) -> str:
    """Search key: accent-free, lowercase, words separated by one space, every script kept."""
    return " ".join(_WORD.findall(strip_accents(_without_symbols(text or "")).lower()))


def _identity_tokens(text: str) -> list[str]:
    raw = _LANG_PREFIX.sub(" ", text or "")
    raw = _RESOLUTION_PAREN.sub(" ", _STATUS_TAG.sub(" ", raw))
    words = [
        w for w in _WORD.findall(strip_accents(_without_symbols(raw)).lower())
        if w not in _QUALITY and not _RESOLUTION.match(w)
    ]
    while len(words) > 1 and words[-1] in _QUALIFIERS:
        words.pop()
    return words


def normalize(text: str) -> str:
    """Channel identity key. 'Canal+ Sport HD' -> 'canalsport', 'TF1 FR' -> 'tf1'."""
    return "".join(_identity_tokens(text))


def loose_key(text: str) -> str:
    """Even looser key used for EPG <-> channel matching."""
    t = normalize(text)
    for suffix in ("tv", "channel", "chaine"):
        if t.endswith(suffix) and len(t) > len(suffix) + 2:
            t = t[: -len(suffix)]
    return t


def contains_any(text: str, keywords: tuple[str, ...] | list[str]) -> bool:
    """Keyword match. Short keywords (<= 3 chars, e.g. 'f1', 'ufc') must match whole words so
    'TF1' does not match 'f1'."""
    n = strip_accents(text or "").lower()
    for k in keywords:
        kk = strip_accents(k).lower().strip()
        if not kk:
            continue
        if len(kk) <= 3:
            if re.search(r"(?<![a-z0-9])" + re.escape(kk) + r"(?![a-z0-9])", n):
                return True
        elif kk in n:
            return True
    return False
