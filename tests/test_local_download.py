"""End-to-end crawl + download test against a local HTTP server."""

from __future__ import annotations

import functools
import http.server
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import unquote, urlparse

from rdownloader.client import DownloadStopped, RDownloaderClient
from rdownloader.config import Options
from rdownloader.util import ListingEntry


def relative_path(url: str) -> str:
    """URL -> slash-separated path relative to the server root."""
    return unquote(urlparse(url).path.lstrip("/"))


def file_name(url: str) -> str:
    return relative_path(url).rsplit("/", 1)[-1]


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # noqa: ANN002 - keep test output clean
        pass


class LocalDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.site = TemporaryDirectory()
        root = Path(cls.site.name)
        (root / "Alpha.7z").write_bytes(b"a" * 2048)
        (root / "Beta.bin").write_bytes(b"b" * 512)
        disc = root / "Disc 1"
        disc.mkdir()
        (disc / "Gamma.bin").write_bytes(b"g" * 1024)
        (disc / "notes.txt").write_text("not a rom")

        handler = functools.partial(_QuietHandler, directory=str(root))
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.site.cleanup()

    def setUp(self) -> None:
        self.output = TemporaryDirectory()
        self.addCleanup(self.output.cleanup)
        self.downloads = Path(self.output.name) / "downloads"

    def _client(self, **overrides) -> RDownloaderClient:
        opts = Options(output=Path(self.output.name), workers=1, live_bar=False,
                       quiet=True, timeout=10, check_size=True, **overrides)
        client = RDownloaderClient(opts)
        client.start = self.url
        return client

    def test_crawl_finds_nested_files(self) -> None:
        files = self._client().crawl(self.url)
        self.assertEqual(sorted(relative_path(entry.url) for entry in files), [
            "Alpha.7z",
            "Beta.bin",
            "Disc 1/Gamma.bin",
            "Disc 1/notes.txt",
        ])

    def test_crawl_respects_extension_filter(self) -> None:
        files = self._client(filter_exts=(".7z",)).crawl(self.url)
        self.assertEqual([file_name(entry.url) for entry in files], ["Alpha.7z"])

    def test_crawl_can_skip_subfolders(self) -> None:
        files = self._client(recursive=False).crawl(self.url)
        self.assertEqual(sorted(file_name(entry.url) for entry in files),
                         ["Alpha.7z", "Beta.bin"])

    def test_crawl_uses_parallel_scanning(self) -> None:
        files = self._client(scan_workers=4).crawl(self.url, scan_workers=4)
        self.assertEqual(len(files), 4)

    def test_crawl_includes_extra_roots(self) -> None:
        client = self._client()
        pages = {
            "https://host/A": [ListingEntry("https://host/A/a.7z", "a.7z", None, "file")],
            "https://host/B": [ListingEntry("https://host/B/b.7z", "b.7z", None, "file")],
        }
        client.fetch_entries = lambda url: pages[url]  # type: ignore[assignment]
        files = client.crawl("https://host/A", extra_starts=("https://host/B",))
        self.assertEqual({entry.url for entry in files},
                         {"https://host/A/a.7z", "https://host/B/b.7z"})

    def test_downloads_files_and_skips_on_second_run(self) -> None:
        client = self._client()
        files = client.crawl(self.url)
        for entry in files:
            self.assertEqual(client.download_file(entry.url).status, "downloaded")

        self.assertEqual((self.downloads / "Alpha.7z").read_bytes(), b"a" * 2048)
        self.assertTrue((self.downloads / "Disc 1" / "Gamma.bin").exists())

        self.assertEqual(client.download_file(files[0].url).status, "skipped")

    def test_resumes_truncated_file(self) -> None:
        client = self._client()
        target = self.downloads / "Beta.bin"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"b" * 100)  # simulate a broken transfer

        result = client.download_file(self.url + "Beta.bin")
        self.assertIn(result.status, ("resumed", "downloaded"))
        self.assertEqual(target.stat().st_size, 512)

    def test_overwrite_replaces_existing_file(self) -> None:
        client = self._client()
        target = self.downloads / "Beta.bin"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"z" * 2000)
        self.assertEqual(client.download_file(self.url + "Beta.bin").status, "skipped")

        result = client.download_file(self.url + "Beta.bin", overwrite=True)
        self.assertEqual(result.status, "downloaded")
        self.assertEqual(target.read_bytes(), b"b" * 512)

    def test_target_override_writes_to_given_path(self) -> None:
        client = self._client()
        alt = Path(self.output.name) / "custom" / "renamed.bin"
        result = client.download_file(self.url + "Beta.bin", target_override=alt,
                                      overwrite=True)
        self.assertEqual(result.status, "downloaded")
        self.assertEqual(alt.read_bytes(), b"b" * 512)

    def test_stop_request_raises(self) -> None:
        client = self._client()
        with self.assertRaises(DownloadStopped):
            client.download_file(self.url + "Alpha.7z", stop_check=lambda: True)


if __name__ == "__main__":
    unittest.main()
