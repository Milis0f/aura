from aura.services import epg, m3u_parser, xtream
from aura.services.textutil import loose_key, normalize

SAMPLE = """#EXTM3U url-tvg="http://example.com/epg.xml.gz"
#EXTINF:-1 tvg-id="TF1.fr" tvg-name="TF1" tvg-logo="http://logo/tf1.png" group-title="France",TF1 HD
#EXTVLCOPT:http-user-agent=VLC/3
http://host/live/u/p/1.m3u8
#EXTINF:-1 group-title="Films",Interstellar (2014)
http://host/movie/u/p/55.mkv
#EXTINF:-1 group-title="Séries",Breaking Bad
http://host/series/u/p/900.mp4
#EXTINF:0,No attributes at all
rtmp://host/app/stream
#EXTGRP:Sports
#EXTINF:-1 tvg-id="" ,beIN Sports 1
http://host/live/u/p/2.ts
"""


def test_parse_m3u_items_and_header():
    parsed = m3u_parser.parse_m3u(SAMPLE, "src1")
    assert parsed.epg_url == "http://example.com/epg.xml.gz"
    assert len(parsed.items) == 5
    tf1 = parsed.items[0]
    assert tf1.name == "TF1 HD"
    assert tf1.tvg_id == "TF1.fr"
    assert tf1.group == "France"
    assert tf1.logo.endswith("tf1.png")
    assert tf1.kind == "live"
    assert tf1.extra["http-user-agent"] == "VLC/3"
    assert tf1.source_id == "src1"


def test_parse_m3u_kind_detection():
    parsed = m3u_parser.parse_m3u(SAMPLE)
    kinds = [i.kind for i in parsed.items]
    assert kinds == ["live", "vod", "series", "live", "live"]


def test_parse_m3u_extgrp_and_bare_lines():
    parsed = m3u_parser.parse_m3u(SAMPLE)
    assert parsed.items[3].name == "No attributes at all"
    assert parsed.items[4].group == "Sports"
    assert parsed.items[4].name == "beIN Sports 1"


def test_parse_m3u_tolerates_garbage():
    parsed = m3u_parser.parse_m3u("garbage\n#EXTINF\nhttp://x/y\n\n#COMMENT\n")
    assert [i.url for i in parsed.items] == ["http://x/y"]  # a line without a scheme is never a stream


def test_stable_ids_are_deterministic():
    a = m3u_parser.parse_m3u(SAMPLE).items[0]
    b = m3u_parser.parse_m3u(SAMPLE).items[0]
    assert a.id == b.id and len(a.id) == 16


def test_roundtrip_to_m3u():
    items = m3u_parser.parse_m3u(SAMPLE).items
    again = m3u_parser.parse_m3u(m3u_parser.to_m3u(items)).items
    assert [i.url for i in again] == [i.url for i in items]


def test_normalize_and_loose_key():
    assert normalize("Canal+ Sport HD") == "canalsport"
    assert normalize("beIN SPORTS 1 FHD") == "beinsports1"
    assert loose_key("TF1 TV") == "tf1"
    assert normalize("Équipe 21") == "equipe21"


def test_xtream_creds_and_urls():
    c = xtream.XtreamCreds.from_any("http://iptv.example:8080/get.php?username=a&password=b", "user", "pa ss")
    assert c.base_url == "http://iptv.example:8080"
    assert xtream.live_url(c, 12, "m3u8") == "http://iptv.example:8080/live/user/pa%20ss/12.m3u8"
    assert xtream.vod_url(c, 5, "") == "http://iptv.example:8080/movie/user/pa%20ss/5.mp4"
    assert "xmltv.php?username=user&password=pa%20ss" in c.epg_url
    assert xtream.pick_live_ext({"allowed_output_formats": ["ts"]}) == "ts"
    assert xtream.pick_live_ext({}) == "m3u8"
    bare = xtream.XtreamCreds.from_any("iptv.example:25461", "u", "p")
    assert bare.base_url == "http://iptv.example:25461"


def test_xtream_conversions():
    c = xtream.XtreamCreds("http://h", "u", "p")
    live = xtream.live_to_items(c, [{"stream_id": 1, "name": "TF1", "category_id": "7", "epg_channel_id": "TF1.fr"}], {"7": "France"}, "m3u8", "s")
    assert live[0].url == "http://h/live/u/p/1.m3u8" and live[0].group == "France" and live[0].tvg_id == "TF1.fr"
    vod = xtream.vod_to_items(c, [{"stream_id": 2, "name": "Film", "container_extension": "mkv", "year": 2020}], {}, "s")
    assert vod[0].url.endswith("/movie/u/p/2.mkv") and vod[0].kind == "vod"
    ser = xtream.series_to_items(c, [{"series_id": 3, "name": "Show", "cover": "c.jpg"}], {}, "s")
    assert ser[0].url == "xtream-series://s/3" and ser[0].kind == "series"
    eps = xtream.episodes_from_info(c, {"episodes": {"1": [{"id": 77, "episode_num": 1, "title": "Pilot", "container_extension": "mp4"}]}})
    assert eps[0]["url"] == "http://h/series/u/p/77.mp4" and eps[0]["season"] == 1


def test_xmltv_time():
    assert epg.parse_xmltv_time("20260910200000 +0200") == epg.parse_xmltv_time("20260910180000 +0000")
    assert epg.parse_xmltv_time("20260910180000") == epg.parse_xmltv_time("20260910180000 +0000")
    assert epg.parse_xmltv_time("garbage") == 0


XMLTV = b"""<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="TF1.fr"><display-name>TF1</display-name><icon src="http://i/tf1.png"/></channel>
  <channel id="C+S.fr"><display-name>Canal+ Sport</display-name></channel>
  <programme start="20260910180000 +0000" stop="20260910200000 +0000" channel="TF1.fr"><title>JT</title><desc>News</desc><category>News</category></programme>
  <programme start="20260910200000 +0000" stop="20260910220000 +0000" channel="TF1.fr"><title>Film du soir</title></programme>
  <programme start="20260910190000 +0000" stop="20260910213000 +0000" channel="C+S.fr"><title>Formula 1 : Grand Prix</title></programme>
</tv>
"""


def test_xmltv_parse_and_store_and_query():
    import gzip

    objs = list(epg.iter_xmltv(XMLTV))
    assert sum(isinstance(o, epg.EpgChannel) for o in objs) == 2
    assert sum(isinstance(o, epg.EpgProgramme) for o in objs) == 3
    # gzip transparently handled; keep_days_past huge so 2026 rows survive regardless of test date
    n_ch, n_pr = epg.store_epg("test", gzip.compress(XMLTV), keep_days_past=100000)
    assert (n_ch, n_pr) == (2, 3)
    assert epg.resolve_epg_id("TF1.fr", "whatever") == "TF1.fr"
    assert epg.resolve_epg_id("", "Canal+ Sport HD") == "C+S.fr"
    assert epg.resolve_epg_id("", "Unknown") is None
    at = epg.parse_xmltv_time("20260910190000 +0000")
    nn = epg.now_next("TF1.fr", at)
    assert [p["title"] for p in nn] == ["JT", "Film du soir"]
    hits = epg.search_programmes(["formula 1"], at - 3600, at + 3600)
    assert hits and hits[0]["channel_id"] == "C+S.fr"


def test_extinf_comma_inside_quotes():
    line = '#EXTINF:-1 tvg-id="x" user-agent="Mozilla/5.0 (X11; Linux) AppleWebKit, like Gecko" group-title="Sports",Canal+ Sport 360'
    attrs, name = m3u_parser.split_extinf(line)
    assert name == "Canal+ Sport 360"
    assert m3u_parser.parse_attrs(attrs)["group-title"] == "Sports"
    assert m3u_parser.split_extinf("#EXTINF:0,No attributes") == ("", "No attributes")
    assert m3u_parser.split_extinf("#EXTINF:-1") == ("", "")
    items = m3u_parser.parse_m3u(line + "\nhttp://h/a.m3u8\n").items
    assert items[0].name == "Canal+ Sport 360" and items[0].group == "Sports"
