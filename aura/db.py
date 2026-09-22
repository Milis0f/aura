"""SQLite persistence. Thin helpers, explicit schema, WAL mode. Connections are short-lived."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import SETTINGS

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  name TEXT NOT NULL,
  url TEXT NOT NULL DEFAULT '',
  username TEXT NOT NULL DEFAULT '',
  password TEXT NOT NULL DEFAULT '',
  epg_url TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  last_refresh INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT '',
  item_count INTEGER NOT NULL DEFAULT 0,
  created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS channels (
  id TEXT NOT NULL,
  source_id TEXT NOT NULL,
  name TEXT NOT NULL,
  name_norm TEXT NOT NULL,
  name_fold TEXT NOT NULL DEFAULT '',
  group_name TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL,
  logo TEXT NOT NULL DEFAULT '',
  tvg_id TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'live',
  extra TEXT NOT NULL DEFAULT '{}',
  sort INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (id, source_id)
);
CREATE INDEX IF NOT EXISTS idx_channels_kind ON channels(kind, group_name);
CREATE INDEX IF NOT EXISTS idx_channels_name ON channels(name_norm);
CREATE INDEX IF NOT EXISTS idx_channels_tvg ON channels(tvg_id);
CREATE TABLE IF NOT EXISTS epg_sources (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  url TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  last_refresh INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT '',
  priority INTEGER NOT NULL DEFAULT 100
);
CREATE TABLE IF NOT EXISTS epg_channels (
  id TEXT NOT NULL,
  epg_source_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  display_norm TEXT NOT NULL,
  icon TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (id, epg_source_id)
);
CREATE INDEX IF NOT EXISTS idx_epgch_norm ON epg_channels(display_norm);
CREATE TABLE IF NOT EXISTS epg_programmes (
  channel_id TEXT NOT NULL,
  epg_source_id TEXT NOT NULL,
  start INTEGER NOT NULL,
  stop INTEGER NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_epg_lookup ON epg_programmes(channel_id, start, stop);
CREATE INDEX IF NOT EXISTS idx_epg_time ON epg_programmes(start, stop);
CREATE TABLE IF NOT EXISTS favorites (
  channel_id TEXT PRIMARY KEY,
  added INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS history (
  channel_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  ts INTEGER NOT NULL,
  position REAL NOT NULL DEFAULT 0,
  duration REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS my_list (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  url TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'vod',
  tmdb_id INTEGER NOT NULL DEFAULT 0,
  poster TEXT NOT NULL DEFAULT '',
  year TEXT NOT NULL DEFAULT '',
  added INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS cache (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  totp_secret TEXT,
  can_write INTEGER NOT NULL DEFAULT 0,
  can_torrent INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  username TEXT NOT NULL,
  csrf TEXT NOT NULL,
  created INTEGER NOT NULL,
  expires INTEGER NOT NULL,
  seen INTEGER NOT NULL,
  ip TEXT NOT NULL DEFAULT '',
  agent TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(username);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  username TEXT,
  ip TEXT,
  action TEXT NOT NULL,
  detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit(username, id DESC);
CREATE TABLE IF NOT EXISTS drives (
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'disk',
  fstype TEXT NOT NULL DEFAULT '',
  device TEXT NOT NULL DEFAULT '',
  mountpoint TEXT NOT NULL DEFAULT '',
  size INTEGER NOT NULL DEFAULT 0,
  removable INTEGER NOT NULL DEFAULT 0,
  managed TEXT NOT NULL DEFAULT '',
  available INTEGER NOT NULL DEFAULT 0,
  last_seen INTEGER NOT NULL DEFAULT 0,
  last_scan INTEGER NOT NULL DEFAULT 0,
  scan_state TEXT NOT NULL DEFAULT '',
  films INTEGER NOT NULL DEFAULT 0,
  episodes INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS media_items (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  title_fold TEXT NOT NULL,
  year TEXT NOT NULL DEFAULT '',
  poster TEXT NOT NULL DEFAULT '',
  backdrop TEXT NOT NULL DEFAULT '',
  overview TEXT NOT NULL DEFAULT '',
  genres TEXT NOT NULL DEFAULT '[]',
  rating REAL NOT NULL DEFAULT 0,
  runtime INTEGER NOT NULL DEFAULT 0,
  url TEXT NOT NULL DEFAULT '',
  art TEXT NOT NULL DEFAULT '',
  meta_state TEXT NOT NULL DEFAULT '',
  added INTEGER NOT NULL,
  updated INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mi_kind ON media_items(kind, added);
CREATE INDEX IF NOT EXISTS idx_mi_fold ON media_items(title_fold);
CREATE INDEX IF NOT EXISTS idx_mi_meta ON media_items(meta_state);
CREATE TABLE IF NOT EXISTS media_files (
  id TEXT PRIMARY KEY,
  drive_id TEXT NOT NULL,
  rel_path TEXT NOT NULL,
  size INTEGER NOT NULL DEFAULT 0,
  mtime INTEGER NOT NULL DEFAULT 0,
  item_id TEXT NOT NULL,
  season INTEGER NOT NULL DEFAULT 0,
  episode INTEGER NOT NULL DEFAULT 0,
  episode_title TEXT NOT NULL DEFAULT '',
  quality TEXT NOT NULL DEFAULT '',
  langs TEXT NOT NULL DEFAULT '',
  art TEXT NOT NULL DEFAULT '',
  duration REAL NOT NULL DEFAULT 0,
  video_codec TEXT NOT NULL DEFAULT '',
  height INTEGER NOT NULL DEFAULT 0,
  probed INTEGER NOT NULL DEFAULT 0,
  added INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mf_item ON media_files(item_id, season, episode);
CREATE INDEX IF NOT EXISTS idx_mf_item_added ON media_files(item_id, added DESC);
CREATE INDEX IF NOT EXISTS idx_mf_drive ON media_files(drive_id);
CREATE TABLE IF NOT EXISTS media_progress (
  file_id TEXT PRIMARY KEY,
  item_id TEXT NOT NULL,
  position REAL NOT NULL DEFAULT 0,
  duration REAL NOT NULL DEFAULT 0,
  finished INTEGER NOT NULL DEFAULT 0,
  updated INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mp_item ON media_progress(item_id, updated);
"""


def _ensure_dirs() -> None:
    for d in (SETTINGS.data_dir, SETTINGS.cache_dir, SETTINGS.uploads_dir):
        Path(d).mkdir(parents=True, exist_ok=True)


_local = threading.local()

PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA temp_store=MEMORY",
    "PRAGMA cache_size=-32000",   # 32 MB page cache
    "PRAGMA mmap_size=268435456",  # 256 MB
)


def connect() -> sqlite3.Connection:
    """One long-lived connection per thread.

    Opening a connection per query cost ~2 ms each; a 400-channel page issued >1000 of them and
    starved the event loop that feeds video segments. Reusing the handle removes that entirely.
    """
    con = getattr(_local, "con", None)
    if con is not None:
        return con
    _ensure_dirs()
    con = sqlite3.connect(SETTINGS.db_path, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    for pragma in PRAGMAS:
        try:
            con.execute(pragma)
        except sqlite3.DatabaseError:  # pragma unsupported on this build: not fatal
            pass
    _local.con = con
    return con


def close_thread_connection() -> None:
    con = getattr(_local, "con", None)
    if con is not None:
        con.close()
        _local.con = None


LATE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_mi_recent ON media_items(kind, last_added DESC);
CREATE INDEX IF NOT EXISTS idx_mi_rating ON media_items(rating DESC, last_added DESC);
CREATE INDEX IF NOT EXISTS idx_mi_year ON media_items(year DESC, title_fold);
"""


def _ensure_column(con: sqlite3.Connection, table: str, column: str, decl: str) -> bool:
    """Add a column to an existing database created by an older version. True when it was just added."""
    if column in {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}:
        return False
    con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    return True


# Sorting a library page used to need MAX(media_files.added) per row, which forced SQLite to join and
# group every title in the collection before it could return sixty. The value is stored on the title
# instead, maintained wherever files are written, and backfilled once here.
BACKFILL_LAST_ADDED = """
UPDATE media_items SET last_added = COALESCE(
  (SELECT MAX(f.added) FROM media_files f WHERE f.item_id = media_items.id), added)
"""


def init_db() -> None:
    con = connect()
    con.executescript(SCHEMA)
    _ensure_column(con, "channels", "name_fold", "TEXT NOT NULL DEFAULT ''")
    if _ensure_column(con, "media_items", "last_added", "INTEGER NOT NULL DEFAULT 0"):
        con.execute(BACKFILL_LAST_ADDED)
    con.executescript(LATE_INDEXES)


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    con = connect()
    if con.in_transaction:  # nested call: join the caller's transaction
        yield con
        return
    try:
        con.execute("BEGIN IMMEDIATE")
        yield con
        con.execute("COMMIT")
    except Exception:
        try:
            con.execute("ROLLBACK")
        except sqlite3.DatabaseError:
            pass
        raise


def query(sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in connect().execute(sql, params).fetchall()]


def query_one(sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
    row = connect().execute(sql, params).fetchone()
    return dict(row) if row else None


def execute(sql: str, params: tuple | dict = ()) -> int:
    return connect().execute(sql, params).rowcount


def chunked(seq, size: int = 400):
    """Split an iterable into chunks that fit SQLite's parameter limit."""
    items = list(seq)
    for i in range(0, len(items), size):
        yield items[i : i + size]


# ---- settings -------------------------------------------------------------

def get_setting(key: str, default: str = "") -> str:
    row = query_one("SELECT value FROM settings WHERE key=?", (key,))
    return row["value"] if row else default


# Bumped on every settings write. core.settings caches coerced values and watches this counter, so a
# value changed by any code path - including one that never heard of the registry - invalidates it.
_settings_version = 0


def settings_version() -> int:
    return _settings_version


def set_setting(key: str, value: str) -> None:
    global _settings_version
    execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    _settings_version += 1


def all_settings() -> dict[str, str]:
    return {r["key"]: r["value"] for r in query("SELECT key, value FROM settings")}


# ---- generic JSON cache ---------------------------------------------------

def cache_get(key: str, max_age: int) -> Any | None:
    row = query_one("SELECT value, ts FROM cache WHERE key=?", (key,))
    if not row or time.time() - row["ts"] > max_age:
        return None
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return None


def cache_set(key: str, value: Any) -> None:
    execute(
        "INSERT INTO cache(key,value,ts) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, ts=excluded.ts",
        (key, json.dumps(value, ensure_ascii=False), int(time.time())),
    )
