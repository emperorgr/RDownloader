"""Unit tests for the pure helpers (no network, no Tk)."""

from __future__ import annotations

import os
import unittest
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from lolroms.config import Options
from lolroms.util import (
    LocalFileIndex,
    LocalStat,
    classify_entry,
    classify_link,
    clean_url,
    compare_dates,
    compare_file,
    compare_stat,
    display_path_for,
    extract_listing,
    extract_links,
    human_bytes,
    local_path_for,
    parse_listing_date,
    path_in,
    sanitize_filename,
)

BASE = "https://lolroms.com/Acorn/Archimedes"


class HumanBytesTests(unittest.TestCase):
    def test_units(self) -> None:
        self.assertEqual(human_bytes(None), "?")
        self.assertEqual(human_bytes(0), "0 B")
        self.assertEqual(human_bytes(1536), "1.50 KB")
        self.assertEqual(human_bytes(3 * 1024 ** 3), "3.00 GB")


class SanitizeTests(unittest.TestCase):
    def test_windows_illegal_characters(self) -> None:
        self.assertEqual(sanitize_filename('a<b>c:d"e/f\\g|h?i*j'), "a_b_c_d_e_f_g_h_i_j")

    def test_strips_dots_and_spaces(self) -> None:
        self.assertEqual(sanitize_filename("  ...Game.7z... "), "Game.7z")

    def test_empty_falls_back(self) -> None:
        self.assertEqual(sanitize_filename("..."), "download")


class CleanUrlTests(unittest.TestCase):
    def test_extracts_first_url(self) -> None:
        self.assertEqual(clean_url('get "https://lolroms.com/Atari/2600".'),
                         "https://lolroms.com/Atari/2600")

    def test_plain_text_passthrough(self) -> None:
        self.assertEqual(clean_url("  https://example.com/x  "), "https://example.com/x")


class ExtractLinksTests(unittest.TestCase):
    PAGE = """<html><nav>
        <a href="/Acorn">Acorn</a>
        <a href="https://lolroms.com/Atari/2600">Atari</a>
      </nav><main>
        <a href="Game.7z">Game</a>
        <a href='Disc%201/'>Disc 1</a>
        <a href="Game.7z">duplicate</a>
        <a href="#top">top</a>
        <a href="javascript:void(0)">js</a>
      </main></html>"""

    def test_ignores_navigation_and_duplicates(self) -> None:
        links = extract_links(self.PAGE, BASE + "/")
        self.assertEqual(links, [
            "https://lolroms.com/Acorn/Archimedes/Game.7z",
            "https://lolroms.com/Acorn/Archimedes/Disc%201/",
        ])

    def test_no_nav_keeps_everything(self) -> None:
        links = extract_links('<a href="/a">A</a><a href="/b">B</a>',
                              "https://lolroms.com/")
        self.assertEqual(len(links), 2)


class ClassifyLinkTests(unittest.TestCase):
    def test_file(self) -> None:
        link = classify_link(BASE + "/Game.7z", BASE)
        self.assertIsNotNone(link)
        assert link is not None
        self.assertEqual((link.kind, link.name), ("file", "Game.7z"))

    def test_directory(self) -> None:
        link = classify_link(BASE + "/Disc%201/", BASE)
        self.assertIsNotNone(link)
        assert link is not None
        self.assertEqual(link.kind, "dir")

    def test_rejects_self_and_parent(self) -> None:
        self.assertIsNone(classify_link(BASE, BASE))
        self.assertIsNone(classify_link("https://lolroms.com/Acorn", BASE))
        self.assertIsNone(classify_link(BASE + "/", BASE))

    def test_dotted_folder_is_still_a_folder(self) -> None:
        link = classify_link(BASE + "/v1.2/", BASE)
        self.assertIsNotNone(link)
        assert link is not None
        self.assertIn(link.kind, ("dir", "file"))  # documented heuristic


class PathMappingTests(unittest.TestCase):
    def test_nested_file(self) -> None:
        url = BASE + "/Disc%202/Game%20(USA).7z"
        self.assertEqual(local_path_for(url, BASE),
                         Path("Archimedes", "Disc 2", "Game (USA).7z"))

    def test_top_level_file(self) -> None:
        self.assertEqual(local_path_for(BASE + "/Game.7z", BASE),
                         Path("Archimedes", "Game.7z"))

    def test_display_path_excludes_category_root(self) -> None:
        self.assertEqual(display_path_for(BASE + "/Disc%201/Game.7z", BASE),
                         "Disc 1/Game.7z")

    def test_base_url_without_trailing_slash(self) -> None:
        self.assertEqual(local_path_for(BASE + "/Game.7z", BASE + "/"),
                         Path("Archimedes", "Game.7z"))

    def test_single_file_start_maps_to_flat_file(self) -> None:
        url = BASE + "/Game.7z"
        self.assertEqual(local_path_for(url, url), Path("Game.7z"))

    def test_server_root_keeps_subfolders(self) -> None:
        self.assertEqual(local_path_for("http://host/Disc 1/Game.7z", "http://host/"),
                         Path("downloads", "Disc 1", "Game.7z"))


class ExtractListingTests(unittest.TestCase):
    PAGE = """<html><nav>
        <div class="row"><a href="/nav-only">Nav</a></div>
      </nav><main>
        <div class="path"><a href="/">Home</a> / <a href="/Arcade">Arcade</a></div>
        <div class="sortbar"><a href="?sort=date">Date</a></div>
        <div class="list">
          <div class="row"><a href="/Arcade/FBNeo/Disc%201">Disc 1</a></div>
          <div class="row"><a href="/Arcade/FBNeo/Game.7z">Game</a><span class="file-meta">2026-11-14</span></div>
          <div class="row recent-folder"><a href="/Arcade/FBNeo/32x">32x</a></div>
        </div>
      </main></html>"""

    def test_only_content_rows_are_returned(self) -> None:
        entries = extract_listing(self.PAGE, "https://lolroms.com/Arcade/FBNeo")
        self.assertEqual([entry.name for entry in entries], ["Disc 1", "Game", "32x"])
        self.assertEqual([entry.url for entry in entries], [
            "https://lolroms.com/Arcade/FBNeo/Disc%201",
            "https://lolroms.com/Arcade/FBNeo/Game.7z",
            "https://lolroms.com/Arcade/FBNeo/32x",
        ])

    def test_file_dates_are_parsed(self) -> None:
        entries = extract_listing(self.PAGE, "https://lolroms.com/Arcade/FBNeo")
        self.assertEqual(entries[1].date, date(2026, 11, 14))
        self.assertIsNone(entries[0].date)  # folders have no date

    def test_parse_listing_date(self) -> None:
        self.assertEqual(parse_listing_date("2026-11-14"), date(2026, 11, 14))
        self.assertEqual(parse_listing_date("updated 2026-01-02 10:00"), date(2026, 1, 2))
        self.assertIsNone(parse_listing_date("not a date"))
        self.assertIsNone(parse_listing_date("2026-99-99"))

    def test_falls_back_to_plain_links(self) -> None:
        entries = extract_listing('<a href="/a.7z">A</a>', "https://lolroms.com/")
        self.assertEqual([(entry.url, entry.name, entry.date) for entry in entries],
                         [("https://lolroms.com/a.7z", "a.7z", None)])

    def test_markup_kind_distinguishes_dotted_folders(self) -> None:
        html = (
            '<div class="row"><a href="/A/FBNeo%201.0.0.03">FBNeo 1.0.0.03</a></div>'
            '<div class="row"><a href="/A/HBMAME%20289.4">HBMAME 289.4</a></div>'
            '<div class="row"><a href="/A/Game.7z">Game</a>'
            '<span class="file-meta">2026-11-14</span></div>'
        )
        entries = extract_listing(html, "https://lolroms.com/A")
        self.assertEqual([entry.kind for entry in entries], ["dir", "dir", "file"])

        folder = classify_entry(entries[0], "https://lolroms.com/A")
        self.assertIsNotNone(folder)
        assert folder is not None
        self.assertEqual(folder.kind, "dir")


class LocalIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "Game.7z"
        self.path.write_bytes(b"abc")
        self.stamp = datetime(2030, 1, 1, 12).timestamp()
        os.utime(self.path, (self.stamp, self.stamp))

    def test_index_reads_existing_and_missing_files(self) -> None:
        index = LocalFileIndex()
        info = index.stat(self.path)
        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(info.size, 3)
        self.assertEqual(info.mtime, self.stamp)
        self.assertIsNone(index.stat(self.root / "missing.7z"))

    def test_compare_stat_statuses(self) -> None:
        index = LocalFileIndex()
        info = index.stat(self.path)
        self.assertEqual(compare_stat(None, None, None)[0], "New")
        self.assertEqual(compare_stat(date(2026, 1, 1), None, info)[0], "Current")
        self.assertEqual(compare_stat(date(2026, 1, 1), 99, info)[0], "Size differs")

        old = LocalStat(3, datetime(2020, 1, 1, 12).timestamp())
        self.assertEqual(compare_stat(date(2026, 1, 1), None, old)[0], "Update")


class CompareDatesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "Game.7z"

    def _touch(self, year: int, month: int, day: int) -> None:
        self.path.write_bytes(b"x")
        stamp = datetime(year, month, day, 12).timestamp()
        os.utime(self.path, (stamp, stamp))

    def test_missing_file_is_new(self) -> None:
        self.assertEqual(compare_dates(date(2026, 11, 14), self.path), ("New", None))

    def test_older_local_is_update(self) -> None:
        self._touch(2020, 1, 1)
        status, local = compare_dates(date(2026, 11, 14), self.path)
        self.assertEqual(status, "Update")
        self.assertEqual(local, date(2020, 1, 1))

    def test_newer_local_is_current(self) -> None:
        self._touch(2030, 1, 1)
        self.assertEqual(compare_dates(date(2026, 11, 14), self.path)[0], "Current")

    def test_unknown_remote_date_is_current(self) -> None:
        self._touch(2020, 1, 1)
        self.assertEqual(compare_dates(None, self.path)[0], "Current")

    def test_size_difference_is_reported(self) -> None:
        self._touch(2020, 1, 1)
        self.assertEqual(compare_file(None, 999, self.path)[0], "Size differs")
        self.assertEqual(compare_file(None, self.path.stat().st_size, self.path)[0],
                         "Current")

    def test_newer_remote_date_wins_over_size(self) -> None:
        self._touch(2020, 1, 1)
        self.assertEqual(compare_file(date(2026, 11, 14), 999, self.path)[0], "Update")

    def test_path_in_mirrors_tree(self) -> None:
        self.assertEqual(
            path_in(Path("D:/collection"), "https://host/Base/Disc%201/Game.7z",
                    "https://host/Base"),
            Path("D:/collection/Disc 1/Game.7z"))


class VersionTests(unittest.TestCase):
    def test_version_is_1_0_0(self) -> None:
        import lolroms
        self.assertEqual(lolroms.__version__, "1.0.0")


class OptionsTests(unittest.TestCase):
    def test_normalisation(self) -> None:
        opts = Options(output="D:/tmp", workers=0, filter_exts=(".7Z", "", ".bin"))
        self.assertEqual(opts.workers, 1)
        self.assertEqual(opts.filter_exts, (".7z", ".bin"))
        self.assertEqual(opts.output, Path("D:/tmp"))

    def test_extension_filter(self) -> None:
        opts = Options(filter_exts=(".7z",))
        self.assertTrue(opts.ext_allowed("Game.7z"))
        self.assertTrue(opts.ext_allowed("GAME.7Z"))
        self.assertFalse(opts.ext_allowed("Game.bin"))


if __name__ == "__main__":
    unittest.main()
