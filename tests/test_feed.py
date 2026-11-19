"""RSS parsing, matching and seen-cache tests."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from lolroms.feed import FeedCache, FeedItem, matches_item, parse_feed

XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Latest ROMs</title>
  <item>
    <title>Pang (World)</title>
    <link>https://lolroms.com/SEGA/Mega%20Drive/Pang%20(World).7z</link>
    <pubDate>Wed, 18 Nov 2026 16:54:53 GMT</pubDate>
    <description>SEGA/Mega Drive/Pang (World).7z</description>
  </item>
  <item>
    <title>Alpha (USA)</title>
    <link>https://lolroms.com/Acorn/Archimedes/Alpha.7z</link>
    <pubDate>Wed, 18 Nov 2026 10:00:00 GMT</pubDate>
    <description>Acorn/Archimedes/Alpha.7z</description>
  </item>
</channel></rss>"""


class ParseFeedTests(unittest.TestCase):
    def test_items_and_dates(self) -> None:
        items = parse_feed(XML)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].title, "Pang (World)")
        self.assertEqual(items[0].path, "SEGA/Mega Drive/Pang (World).7z")
        self.assertEqual(items[0].published,
                         datetime(2026, 11, 18, 16, 54, 53, tzinfo=timezone.utc))

    def test_truncated_feed_returns_empty(self) -> None:
        self.assertEqual(parse_feed("<rss"), [])

    def test_unescaped_ampersand_is_tolerated(self) -> None:
        text = XML.replace("Pang (World)</title>", "Pang & Friends</title>")
        items = parse_feed(text)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].title, "Pang & Friends")


class MatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.item = FeedItem("Pang (World)", "https://lolroms.com/SEGA/Mega%20Drive/Pang%20(World).7z",
                             None, "SEGA/Mega Drive/Pang (World).7z")

    def test_folder_prefix(self) -> None:
        self.assertTrue(matches_item(self.item, "SEGA/Mega Drive"))
        self.assertTrue(matches_item(self.item, "/SEGA/"))
        self.assertFalse(matches_item(self.item, "Acorn"))

    def test_query_and_exact_url(self) -> None:
        self.assertTrue(matches_item(self.item, query="pang"))
        self.assertFalse(matches_item(self.item, query="alpha"))
        self.assertTrue(matches_item(self.item, exact_url=self.item.link))
        self.assertFalse(matches_item(self.item, query="pang", path_prefix="Acorn"))

    def test_no_filter_matches(self) -> None:
        self.assertTrue(matches_item(self.item))


class FeedCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = FeedCache(Path(self.tmp.name) / "feed.json")
        self.items = parse_feed(XML)

    def test_first_run_marks_everything_new(self) -> None:
        new = self.cache.mark(self.items)
        self.assertEqual(new, {item.link for item in self.items})
        self.assertEqual(self.cache.mark(self.items), set())

    def test_only_unseen_links_are_new(self) -> None:
        self.cache.mark(self.items[:1])
        new = self.cache.mark(self.items)
        self.assertEqual(new, {self.items[1].link})

    def test_clear(self) -> None:
        self.cache.mark(self.items)
        self.assertEqual(self.cache.clear(), 1)
        self.assertEqual(self.cache.load(), {})
        self.assertEqual(self.cache.clear(), 0)


if __name__ == "__main__":
    unittest.main()
