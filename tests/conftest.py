import os
import tempfile

import pytest

_tmp = tempfile.mkdtemp(prefix="aura-test-")
os.environ["AURA_DATA"] = _tmp

from aura import db  # noqa: E402  (after env is set)
from aura.services import accounts  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    db.init_db()
    with db.transaction() as con:
        for t in ("channels", "sources", "epg_channels", "epg_programmes", "epg_sources", "favorites", "history", "my_list", "cache", "settings",
                  "users", "sessions", "audit", "drives", "media_items", "media_files", "media_progress"):
            con.execute(f"DELETE FROM {t}")
    accounts.reset_failures()
    yield
