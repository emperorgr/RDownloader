"""Scan cache round-trip tests."""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from rdownloader.cache import ScanCache
from rdownloader.util import ListingEntry

URL = "https://source.invalid/Arcade/FBNeo"


class ScanCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = ScanCache(Path(self.tmp.name) / "scan_cache")
        self.entries = [
            ListingEntry(URL + "/32x/a.7z", "a.7z", date(2026, 11, 14)),
            ListingEntry(URL + "/32x/b.7z", "b.7z", None),
        ]

    def test_round_trip(self) -> None:
        path = self.cache.save(URL, self.entries)
        self.assertIsNotNone(path)
        self.assertTrue(path.exists())

        snapshot = self.cache.load(URL)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.start_url, URL)
        self.assertTrue(snapshot.complete)
        self.assertEqual(list(snapshot.entries), self.entries)

    def test_other_url_misses(self) -> None:
        self.cache.save(URL, self.entries)
        self.assertIsNone(self.cache.load(URL + "/other"))

    def test_trailing_slash_uses_same_cache(self) -> None:
        self.cache.save(URL + "/", self.entries)
        snapshot = self.cache.load(URL)
        self.assertIsNotNone(snapshot)
        snapshot = self.cache.load(URL + "//")
        self.assertIsNotNone(snapshot)

    def test_partial_flag_is_saved(self) -> None:
        self.cache.save(URL, self.entries, complete=False)
        snapshot = self.cache.load(URL)
        assert snapshot is not None
        self.assertFalse(snapshot.complete)

    def test_kinds_round_trip(self) -> None:
        entries = [
            ListingEntry(URL + "/Folder.1.0.3", "Folder.1.0.3", None, "dir"),
            ListingEntry(URL + "/Game.7z", "Game.7z", date(2026, 11, 14), "file"),
        ]
        self.cache.save(URL, entries)
        snapshot = self.cache.load(URL)
        assert snapshot is not None
        self.assertEqual([entry.kind for entry in snapshot.entries], ["dir", "file"])

    def test_corrupt_cache_is_ignored(self) -> None:
        path = self.cache.save(URL, self.entries)
        assert path is not None
        path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(self.cache.load(URL))

    def test_clear(self) -> None:
        self.cache.save(URL, self.entries)
        self.cache.save(URL + "/two", self.entries)
        self.assertEqual(self.cache.clear(), 2)
        self.assertIsNone(self.cache.load(URL))
        self.assertEqual(self.cache.clear(), 0)


if __name__ == "__main__":
    unittest.main()
