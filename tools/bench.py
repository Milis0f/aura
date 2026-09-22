"""Seed a realistic-size library into a throwaway database, then time the endpoints against it.

A development database holds a handful of films, and every timing taken on it is meaningless for a
2000-film box: the queries that collapse at scale look instant on ten rows. This builds 2500 films and
500 series (about 10 000 files) in a database of its own, and reports p50/p95 per route.

    python tools/bench.py

Run it before and after touching a query. The numbers, not the intuition, decide whether it was worth it.
"""
from __future__ import annotations

import os
import random
import sys
import time

SCRATCH = os.path.dirname(os.path.abspath(__file__))
os.environ["AURA_DATA"] = os.path.join(SCRATCH, "bench-data")

# The venv holds an editable install pointing at the MAIN checkout. A script living outside the worktree
# therefore imports the wrong copy of aura, and every timing is taken on code that is not the one under
# test. The worktree goes first, explicitly.
WORKTREE = os.environ.get("AURA_WORKTREE") or os.getcwd()
sys.path.insert(0, WORKTREE)

from aura import db  # noqa: E402
from aura.main import create_app  # noqa: E402
from aura.services import accounts  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

FILMS = 2500
SERIES = 500
WORDS = ("noir", "atlas", "vertige", "orage", "cendre", "lumen", "sillage", "aurore", "kilo", "delta",
         "solstice", "marée", "granit", "echo", "fauve", "nadir", "brume", "onyx", "pulsar", "sable")


def fold(text: str) -> str:
    return text.lower()


def seed() -> None:
    random.seed(7)
    now = int(time.time())
    items, files, progress = [], [], []
    for n in range(FILMS):
        title = f"{random.choice(WORDS).capitalize()} {random.choice(WORDS)} {n}"
        iid = f"film:{n}"
        items.append((iid, "film", title, fold(title), str(1950 + n % 75), "", "", "Un résumé.", "[]",
                      round(random.uniform(4, 9), 1), random.randint(80, 170), "", "", "done",
                      now - n * 60, now))
        fid = f"f:{n}"
        files.append((fid, f"drive{n % 4}", f"Films/{title}/{title}.mkv", random.randint(1, 40) * 10**9,
                      now, iid, 0, 0, "", random.choice(("1080p", "2160p", "720p")),
                      random.choice(("FR", "EN", "MULTI")), "", random.uniform(4000, 9000), "h264",
                      1080, 1, now - n * 60))
        if n % 12 == 0:
            progress.append((fid, iid, random.uniform(100, 3000), 6000, 0, now - n))
    for s in range(SERIES):
        title = f"Série {random.choice(WORDS)} {s}"
        iid = f"tv:{s}"
        items.append((iid, "series", title, fold(title), str(1990 + s % 35), "", "", "Un résumé.", "[]",
                      round(random.uniform(4, 9), 1), 45, "", "", "done", now - s * 90, now))
        for ep in range(random.randint(8, 24)):
            fid = f"e:{s}:{ep}"
            files.append((fid, f"drive{s % 4}", f"Series/{title}/S01E{ep:02d}.mkv", 2 * 10**9, now, iid,
                          1, ep + 1, f"Épisode {ep + 1}", "1080p", "FR", "", 2700.0, "h264", 1080, 1, now))

    with db.transaction() as con:
        con.execute("DELETE FROM media_items")
        con.execute("DELETE FROM media_files")
        con.execute("DELETE FROM media_progress")
        con.executemany("INSERT INTO media_items(id, kind, title, title_fold, year, poster, backdrop, overview,"
                        " genres, rating, runtime, url, art, meta_state, added, updated)"
                        " VALUES (" + ",".join("?" * 16) + ")", items)
        con.executemany("INSERT INTO media_files(id, drive_id, rel_path, size, mtime, item_id, season, episode,"
                        " episode_title, quality, langs, art, duration, video_codec, height, probed, added)"
                        " VALUES (" + ",".join("?" * 17) + ")", files)
        con.executemany("INSERT INTO media_progress VALUES (?,?,?,?,?,?)", progress)
        # available = 1: on a real box the disks are plugged in, and that is what the filter short-circuits on
        con.execute("INSERT OR REPLACE INTO drives(id, label, kind, available, last_seen) VALUES "
                    "('drive0','D0','disk',1,?),('drive1','D1','disk',1,?),"
                    "('drive2','D2','disk',1,?),('drive3','D3','disk',1,?)",
                    (now, now, now, now))
        # the old code has no such column; the baseline must still run on identical data
        if "last_added" in {r["name"] for r in con.execute("PRAGMA table_info(media_items)")}:
            con.execute("UPDATE media_items SET last_added = COALESCE("
                        "(SELECT MAX(f.added) FROM media_files f WHERE f.item_id = media_items.id), added)")
    print(f"seeded: {len(items)} items, {len(files)} files, {len(progress)} progress rows")


def _has_drives(con) -> bool:
    return bool(con.execute("SELECT 1 FROM sqlite_master WHERE name='drives'").fetchone())


def bench(client, path: str, rounds: int = 15) -> tuple[float, float, int]:
    times = []
    status = 0
    for _ in range(rounds):
        start = time.perf_counter()
        response = client.get(path)
        times.append((time.perf_counter() - start) * 1000)
        status = response.status_code
    times.sort()
    return times[len(times) // 2], times[int(len(times) * 0.95)], status


def main() -> None:
    db.init_db()
    seed()
    if not accounts.get_user("bench"):
        accounts.create_user("bench", "bench-only-throwaway-passphrase", True, True)
    with TestClient(create_app()) as client:
        client.portal.call(client.app.state.aura.library.settle)
        token, _ = accounts.open_session(accounts.get_user("bench"), "127.0.0.1", "bench")
        client.cookies.set("aura_session", token)
        routes = [
            "/api/library/home",
            "/api/library/items?limit=60",
            "/api/library/items?limit=60&sort=title",
            "/api/library/items?limit=60&sort=rating",
            "/api/library/items?limit=60&offset=2000",
            "/api/library/items?limit=60&q=orage",
            "/api/library/items?limit=60&kind=series",
            "/api/home",
            "/api/stats",
        ]
        print(f"\n{'route':46} {'p50':>9} {'p95':>9}")
        print("-" * 68)
        for path in routes:
            p50, p95, status = bench(client, path)
            flag = "  <-- LENT" if p50 > 50 else ""
            print(f"{path:46} {p50:8.1f}ms {p95:8.1f}ms  [{status}]{flag}")


if __name__ == "__main__":
    sys.exit(main())
