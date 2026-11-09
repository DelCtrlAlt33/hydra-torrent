"""
Tests for search.py's pure/mockable functions: parse_size_to_bytes (pure)
and search_jackett (pure XML parsing once the HTTP call is mocked out).

"""
from unittest.mock import Mock, patch

import pytest

from search import parse_size_to_bytes, search_jackett


# ---------------------------------------------------------------------------
# parse_size_to_bytes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("size_str,expected", [
    ("1.5 GB", int(1.5 * 1024**3)),
    ("1,5 GB", int(1.5 * 1024**3)),      # comma-as-decimal (EU formatting)
    ("2.5 GiB", int(2.5 * 1024**3)),
    ("500 MB", 500 * 1024**2),
    ("500 MiB", 500 * 1024**2),
    ("700 KB", 700 * 1024),
    ("1024", 1024),                       # no unit → raw bytes
    ("", 0),
    (None, 0),
    ("not a size at all", 0),
])
def test_parse_size_to_bytes(size_str, expected):
    assert parse_size_to_bytes(size_str) == expected


def test_parse_size_to_bytes_never_raises_on_garbage():
    # This runs on untrusted, external HTML/XML text - it must degrade to
    # 0 rather than crash the whole search pipeline over one bad row.
    assert parse_size_to_bytes("¯\\_(ツ)_/¯") == 0


# ---------------------------------------------------------------------------
# search_jackett - mocked HTTP, real XML parsing
# ---------------------------------------------------------------------------

TORZNAB_NS = "http://torznab.com/schemas/2015/feed"

SAMPLE_TORZNAB_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="{TORZNAB_NS}">
<channel>
  <item>
    <title>Show.Name.S01E05.1080p.WEB-DL</title>
    <link>https://indexer.example/details/123</link>
    <comments>https://indexer.example/comments/123</comments>
    <pubDate>Mon, 01 Jan 2026 12:00:00 +0000</pubDate>
    <size>1610612736</size>
    <enclosure url="magnet:?xt=urn:btih:abc123&amp;dn=Show.Name.S01E05" length="1610612736" type="application/x-bittorrent" />
    <torznab:attr name="seeders" value="42" />
    <torznab:attr name="peers" value="55" />
    <torznab:attr name="indexer" value="ExampleIndexer" />
  </item>
  <item>
    <title>Movie.Name.2024.1080p.BluRay</title>
    <link>https://indexer.example/download/456.torrent</link>
    <comments>https://indexer.example/comments/456</comments>
    <pubDate>Tue, 02 Jan 2026 08:30:00 +0000</pubDate>
    <size>4294967296</size>
    <enclosure url="https://indexer.example/download/456.torrent" length="4294967296" type="application/x-bittorrent" />
    <torznab:attr name="seeders" value="10" />
    <torznab:attr name="leechers" value="3" />
  </item>
  <item>
    <title>No.Magnet.Or.Link.Result</title>
    <pubDate>Wed, 03 Jan 2026 00:00:00 +0000</pubDate>
    <size>0</size>
  </item>
</channel>
</rss>"""


def _mock_response(xml_text, status_code=200):
    resp = Mock()
    resp.status_code = status_code
    resp.content = xml_text.encode("utf-8")
    return resp


def test_search_jackett_parses_magnet_from_enclosure():
    with patch("search.requests.get", return_value=_mock_response(SAMPLE_TORZNAB_XML)):
        results = search_jackett("http://localhost:9117", "show name", "fakekey")

    tv_result = next(r for r in results if "Show.Name" in r["filename"])
    assert tv_result["magnet"].startswith("magnet:?xt=urn:btih:abc123")
    assert tv_result["seeders"] == 42
    assert tv_result["source"] == "ExampleIndexer"


def test_search_jackett_unescapes_double_escaped_titles():
    # Some indexers send "&amp;amp;" inside the XML; the parser decodes it once,
    # so without an extra unescape the UI shows a literal "&amp;".
    xml = SAMPLE_TORZNAB_XML.replace(
        "<title>Show.Name.S01E05.1080p.WEB-DL</title>",
        "<title>Tom &amp;amp; Jerry S01E05</title>",
    )
    with patch("search.requests.get", return_value=_mock_response(xml)):
        results = search_jackett("http://localhost:9117", "tom", "fakekey")

    assert results[0]["filename"] == "Tom & Jerry S01E05"


def test_search_jackett_falls_back_to_torrent_url_when_no_magnet():
    with patch("search.requests.get", return_value=_mock_response(SAMPLE_TORZNAB_XML)):
        results = search_jackett("http://localhost:9117", "movie name", "fakekey")

    movie_result = next(r for r in results if "Movie.Name" in r["filename"])
    assert movie_result["magnet"] is None
    assert movie_result["torrent_url"] == "https://indexer.example/download/456.torrent"


def test_search_jackett_derives_leechers_from_peers_minus_seeders():
    # The first item has seeders=42, peers=55, no explicit leechers attr -
    # search_jackett must derive leechers = peers - seeders rather than
    # leaving it at 0.
    with patch("search.requests.get", return_value=_mock_response(SAMPLE_TORZNAB_XML)):
        results = search_jackett("http://localhost:9117", "show name", "fakekey")

    tv_result = next(r for r in results if "Show.Name" in r["filename"])
    assert tv_result["leechers"] == 55 - 42


def test_search_jackett_skips_items_with_no_magnet_and_no_link():
    with patch("search.requests.get", return_value=_mock_response(SAMPLE_TORZNAB_XML)):
        results = search_jackett("http://localhost:9117", "anything", "fakekey")

    titles = [r["filename"] for r in results]
    assert "No.Magnet.Or.Link.Result" not in titles
    assert len(results) == 2  # only the two parseable items


def test_search_jackett_raises_on_non_200():
    with patch("search.requests.get", return_value=_mock_response("", status_code=500)):
        with pytest.raises(Exception, match="500"):
            search_jackett("http://localhost:9117", "anything", "fakekey")


def test_search_jackett_sends_api_key_and_query_as_params():
    mock_get = Mock(return_value=_mock_response(SAMPLE_TORZNAB_XML))
    with patch("search.requests.get", mock_get):
        search_jackett("http://localhost:9117", "my query", "secret-key-123")

    _, kwargs = mock_get.call_args
    assert kwargs["params"]["apikey"] == "secret-key-123"
    assert kwargs["params"]["q"] == "my query"


# ---------------------------------------------------------------------------
# 2026-11-10 audit: one malformed item failed the whole search.
# ---------------------------------------------------------------------------

def test_search_jackett_skips_a_malformed_item_and_keeps_the_rest():
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="{TORZNAB_NS}">
<channel>
  <item>
    <title>Broken.Release.No.PubDate</title>
    <comments>https://indexer.example/1</comments>
    <size>100</size>
    <enclosure url="magnet:?xt=urn:btih:aaa" length="100" type="application/x-bittorrent" />
  </item>
  <item>
    <title>Broken.Release.Bad.Seeders</title>
    <comments>https://indexer.example/2</comments>
    <pubDate>Mon, 01 Jan 2026 12:00:00 +0000</pubDate>
    <size>100</size>
    <enclosure url="magnet:?xt=urn:btih:bbb" length="100" type="application/x-bittorrent" />
    <torznab:attr name="seeders" value="lots" />
  </item>
  <item>
    <title>Good.Release.1080p</title>
    <comments>https://indexer.example/3</comments>
    <pubDate>Mon, 01 Jan 2026 12:00:00 +0000</pubDate>
    <size>100</size>
    <enclosure url="magnet:?xt=urn:btih:ccc" length="100" type="application/x-bittorrent" />
    <torznab:attr name="seeders" value="7" />
  </item>
</channel>
</rss>"""
    response = Mock(status_code=200, content=xml.encode("utf-8"))
    with patch("search.requests.get", return_value=response):
        results = search_jackett("http://jackett.local:9117", "release", "key")

    assert [r["filename"] for r in results] == ["Good.Release.1080p"]
    assert results[0]["seeders"] == 7
