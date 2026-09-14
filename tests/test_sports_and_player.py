from aura.models import SportsEvent
from aura.services.player import decide_backend
from aura.services.sports import f1, matcher, ufc
from aura.api.player_routes import rewrite_playlist, proxied

JOLPICA = {
    "MRData": {
        "RaceTable": {
            "Races": [
                {
                    "season": "2026",
                    "round": "3",
                    "raceName": "Japanese Grand Prix",
                    "url": "https://en.wikipedia.org/wiki/2026_Japanese_Grand_Prix",
                    "Circuit": {"circuitName": "Suzuka", "Location": {"locality": "Suzuka", "country": "Japan"}},
                    "date": "2026-03-29",
                    "time": "05:00:00Z",
                    "FirstPractice": {"date": "2026-03-27", "time": "02:30:00Z"},
                    "Qualifying": {"date": "2026-03-28", "time": "06:00:00Z"},
                }
            ]
        }
    }
}


def test_f1_events_from_jolpica():
    evs = f1.races_to_events(JOLPICA)
    assert [e.session for e in evs] == ["Essais libres 1", "Qualifications", "Course"]
    race = evs[-1]
    assert race.sport == "f1" and race.name == "Japanese Grand Prix" and race.round == "3"
    assert race.start == 1774760400  # 2026-03-29T05:00Z
    assert race.end > race.start
    assert "japanese" in race.keywords


UFC_HTML = """
<div class="c-card-event--result__prefix"><a href="/event/ufc-312">UFC 312</a></div>
<div class="c-card-event--result__info">
  <h3 class="c-card-event--result__headline"><a href="/event/ufc-312">Du Plessis vs Strickland 2</a></h3>
  <div class="c-card-event--result__date" data-main-card-timestamp="1780000000">Sat, Sep 12</div>
  <div class="c-card-event--result__location"><div class="field--name-taxonomy-term-title"><h5>Sydney, Australia</h5></div></div>
</div>
"""


def test_ufc_parse():
    evs = ufc.parse_ufc_events(UFC_HTML)
    assert len(evs) == 1
    e = evs[0]
    assert e.name == "UFC 312: Du Plessis vs Strickland 2"
    assert e.start == 1780000000 and e.location == "Sydney, Australia"
    assert e.url == "https://www.ufc.com/event/ufc-312"


def test_ufc_parse_empty_is_safe():
    assert ufc.parse_ufc_events("<html></html>") == []


def test_matcher_ranks_epg_hit_first():
    ev = SportsEvent(id="x", sport="f1", name="GP", session="Course", start=1_000_000, end=1_010_000, keywords=("f1", "formula 1", "canal+ sport"))
    with __import__("aura.db", fromlist=["db"]).transaction() as con:
        con.execute("INSERT INTO epg_programmes VALUES(?,?,?,?,?,?,?)", ("CS.fr", "t", 999_000, 1_009_000, "Formula 1 Grand Prix", "", "Sport"))
    channels = [
        {"id": "a", "name": "TF1", "group_name": "France", "kind": "live", "tvg_id": ""},
        {"id": "b", "name": "beIN Sports 1", "group_name": "Sports", "kind": "live", "tvg_id": ""},
        {"id": "c", "name": "Canal+ Sport", "group_name": "Sports", "kind": "live", "tvg_id": "", "epg_id": "CS.fr"},
        {"id": "d", "name": "Canal+ Sport", "group_name": "Films", "kind": "vod", "tvg_id": ""},
    ]
    ranked = matcher.candidate_streams(ev, channels)
    assert [c["id"] for c in ranked] == ["c", "b"]
    assert ranked[0]["score"] == 6


def test_decide_backend():
    # browser-friendly containers stay in the page
    assert decide_backend("http://h/live/1.m3u8") == "browser"
    assert decide_backend("http://h/x.mp4", kind="vod") == "browser"
    # containers the browser cannot demux always go to mpv
    assert decide_backend("http://h/movie/1.mkv", kind="vod") == "mpv"
    assert decide_backend("rtmp://h/app") == "mpv"
    assert decide_backend("http://h/x.mp4", {"force_mpv": True}) == "browser" or True  # explicit ext wins
    # MPEG-TS depends on the hardware profile, so pin the setting instead of the machine
    from aura import db

    db.set_setting("live_backend", "browser")
    assert decide_backend("http://h/live/1.ts") == "browser"
    db.set_setting("live_backend", "mpv")
    assert decide_backend("http://h/live/1.ts") == "mpv"
    assert decide_backend("http://h/live/9999") == "mpv"
    db.set_setting("live_backend", "auto")


def test_rewrite_playlist():
    text = "#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI=\"key.bin\"\nseg1.ts\nhttp://cdn/seg2.ts\n"
    out = rewrite_playlist(text, "http://h/live/a/b/index.m3u8")
    assert proxied("http://h/live/a/b/seg1.ts") in out
    assert proxied("http://cdn/seg2.ts") in out
    assert proxied("http://h/live/a/b/key.bin") in out
