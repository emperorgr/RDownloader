"""HTTP engine: recursive crawling plus resumable, retrying downloads."""

from __future__ import annotations

import concurrent.futures as cf
import random
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from .config import (
    CHUNK_SIZE,
    LISTING_TIMEOUT,
    MAX_ATTEMPTS,
    MAX_BACKOFF,
    RETRYABLE_STATUS,
    USER_AGENT,
    Options,
)
from .feed import RSS_URL
from .util import (
    ListingEntry,
    ProgressBar,
    classify_entry,
    extract_listing,
    human_bytes,
    local_path_for,
)

DOWNLOADED = "downloaded"
RESUMED = "resumed"
SKIPPED = "skipped"
ALREADY_COMPLETE = "complete"


class DownloadStopped(Exception):
    """Raised when the user asks to stop during a transfer."""


@dataclass(frozen=True, slots=True)
class DownloadResult:
    path: Path
    status: str  # downloaded | resumed | skipped | complete

    @property
    def size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0


def _backoff(attempt: int, cap: float = MAX_BACKOFF) -> float:
    return min(cap, 2.0 ** attempt) + random.random()


class LoLRomsClient:
    """Talks to the site: crawls listings and downloads individual files."""

    def __init__(self, opts: Options) -> None:
        self.opts = opts
        #: Start URL, used both as crawl root and as path-mapping base.
        self.start: str | None = None
        self._local = threading.local()
        self._log_lock = threading.Lock()

    # -- sessions ---------------------------------------------------------- #
    @property
    def session(self) -> requests.Session:
        """One session per thread: safe under the worker pool, reuses sockets."""
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update({"User-Agent": USER_AGENT,
                                    "X-LoLRoms-Token": self.opts.token})
            self._local.session = session
        return session

    # -- logging ----------------------------------------------------------- #
    def _log(self, message: str) -> None:
        with self._log_lock:
            if not self.opts.quiet:
                print(message, flush=True)
            if self.opts.log_cb is not None:
                try:
                    self.opts.log_cb(message)
                except Exception:  # noqa: BLE001 - callbacks must not kill a transfer
                    pass

    # -- HTTP with retry --------------------------------------------------- #
    def _get(self, url: str, *, timeout: float | None = None,
             attempts: int | None = None, **kwargs) -> requests.Response:
        timeout = self.opts.timeout if timeout is None else timeout
        attempts = MAX_ATTEMPTS if attempts is None else attempts
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                response = self.session.get(url, timeout=timeout, **kwargs)
                if response.status_code in RETRYABLE_STATUS:
                    raise requests.HTTPError(f"HTTP {response.status_code}", response=response)
                return response
            except requests.RequestException as error:
                last_error = error
                if attempt == attempts:
                    break
                delay = _backoff(attempt, cap=15.0)
                self._log(f"    retry {attempt}/{attempts - 1} after: {error} "
                          f"(sleeping {delay:.1f}s)")
                time.sleep(delay)

        raise last_error  # type: ignore[misc]

    def fetch_entries(self, url: str) -> list[ListingEntry]:
        response = self._get(url, timeout=LISTING_TIMEOUT, attempts=2)
        if response.status_code != 200:
            raise RuntimeError(f"Listing failed: HTTP {response.status_code} for {url}")
        return extract_listing(response.text, url)

    def fetch_feed(self) -> str:
        """Download the site's RSS feed as text."""
        response = self._get(RSS_URL, timeout=LISTING_TIMEOUT, attempts=2)
        response.raise_for_status()
        return response.text

    def fetch_links(self, url: str) -> list[str]:
        return [entry.url for entry in self.fetch_entries(url)]

    # -- crawling ---------------------------------------------------------- #
    def crawl(self, start_url: str, stop_check=None, scan_workers: int = 4,
              extra_starts=()) -> list[ListingEntry]:
        """Breadth-first walk of the category tree, returning files and dates."""
        files: list[ListingEntry] = []
        seen_files: set[str] = set()
        starts = [start_url]
        for extra in extra_starts:
            if extra not in starts:
                starts.append(extra)
        seen_dirs: set[str] = set(starts)
        pending: list[str] = list(starts)
        scanned_pages = 0
        first_error: Exception | None = None

        def consume(directory_url: str, entries: list[ListingEntry]) -> None:
            nonlocal scanned_pages
            scanned_pages += 1
            for entry in entries:
                child = classify_entry(entry, directory_url)
                if child is None:
                    continue
                if child.kind == "file":
                    if child.url in seen_files or not self.opts.ext_allowed(child.name):
                        continue
                    seen_files.add(child.url)
                    files.append(ListingEntry(child.url, child.name, entry.date, "file"))
                elif self.opts.recursive and child.url not in seen_dirs:
                    seen_dirs.add(child.url)
                    pending.append(child.url)

        def note_failure(directory_url: str, error: Exception) -> None:
            nonlocal first_error
            if first_error is None:
                first_error = error
            self._log(f"  [scan] {directory_url} failed: {error}")

        if scan_workers <= 1:
            while pending:
                if stop_check and stop_check():
                    self._log("Scan stopped by user \u2014 returning files found so far.")
                    break
                directory_url = pending.pop(0)
                self._log(f"[scan] {directory_url}")
                try:
                    entries = self.fetch_entries(directory_url)
                except Exception as error:  # noqa: BLE001 - keep scanning other folders
                    note_failure(directory_url, error)
                    continue
                consume(directory_url, entries)
                self._log(f"  [scan] {directory_url} done | "
                          f"total files found: {len(files)}")
        else:
            with cf.ThreadPoolExecutor(max_workers=scan_workers) as pool:
                while pending:
                    if stop_check and stop_check():
                        self._log("Scan stopped by user \u2014 returning files found so far.")
                        break
                    batch = pending[:scan_workers]
                    del pending[:scan_workers]
                    futures: dict[cf.Future, str] = {}
                    for directory_url in batch:
                        self._log(f"[scan] {directory_url}")
                        futures[pool.submit(self.fetch_entries, directory_url)] = directory_url
                    for future in cf.as_completed(futures):
                        directory_url = futures[future]
                        try:
                            entries = future.result()
                        except Exception as error:  # noqa: BLE001
                            note_failure(directory_url, error)
                            continue
                        consume(directory_url, entries)
                        self._log(f"  [scan] {directory_url} done | "
                                  f"total files found: {len(files)}")

        if scanned_pages == 0 and first_error is not None:
            raise first_error
        return files

    # -- output paths ------------------------------------------------------ #
    def target_path(self, file_url: str) -> Path:
        return self.opts.output / local_path_for(file_url, self.start or file_url)

    @staticmethod
    def ensure_directory(directory: Path) -> None:
        """Create *directory*, removing files that would block it."""
        for candidate in reversed((directory, *directory.parents)):
            if candidate.is_file():
                candidate.unlink()
        directory.mkdir(parents=True, exist_ok=True)

    # -- remote size ------------------------------------------------------- #
    def remote_size(self, url: str) -> int | None:
        """Remote file size in bytes, or ``None`` when it cannot be determined."""
        try:
            response = self.session.head(url, timeout=self.opts.timeout)
            length = response.headers.get("Content-Length")
            if length and length.isdigit():
                return int(length)
        except requests.RequestException:
            pass

        # Some servers reject HEAD; probe with a single-byte range instead.
        try:
            with self.session.get(url, headers={"Range": "bytes=0-0"}, stream=True,
                                  timeout=self.opts.timeout) as response:
                return self._total_size(response, 0)
        except requests.RequestException:
            return None

    @staticmethod
    def _total_size(response: requests.Response, start: int) -> int | None:
        content_range = response.headers.get("Content-Range")
        if content_range and "/" in content_range:
            tail = content_range.rsplit("/", 1)[1]
            if tail.isdigit():
                return int(tail)
        length = response.headers.get("Content-Length")
        if length and length.isdigit():
            return start + int(length)
        return None

    # -- one file: skip-if-complete, resume, retry ------------------------- #
    def download_file(self, file_url: str, stop_check=None,
                      overwrite: bool = False,
                      target_override: Path | None = None) -> DownloadResult:
        target = Path(target_override) if target_override else self.target_path(file_url)
        self.ensure_directory(target.parent)
        name = target.name

        start = 0
        if not overwrite:
            if self.opts.check_size and target.exists():
                remote = self.remote_size(file_url)
                if remote is None or target.stat().st_size >= remote:
                    return DownloadResult(target, SKIPPED)
            start = target.stat().st_size if (target.exists() and self.opts.resume) else 0
        initial = start
        use_bar = self.opts.live_bar and self.opts.workers == 1
        last_error: Exception | None = None
        truncated = False

        for attempt in range(1, MAX_ATTEMPTS + 1):
            if stop_check and stop_check():
                raise DownloadStopped(name)

            headers = {"Range": f"bytes={start}-"} if start and self.opts.resume else {}
            wrote_any = False

            try:
                with self.session.get(file_url, stream=True, headers=headers,
                                      timeout=self.opts.timeout) as response:
                    if response.status_code == 416:  # nothing left to send
                        return DownloadResult(target, ALREADY_COMPLETE)
                    response.raise_for_status()
                    if start and response.status_code == 200:  # server ignored Range
                        start = 0

                    total = self._total_size(response, start)
                    bar = ProgressBar(name, (total - start) if total else None) if use_bar else None
                    callback = self.opts.progress_cb
                    current = start
                    last_time = time.monotonic()
                    last_bytes = current

                    with open(target, "ab" if start else "wb") as handle:
                        if not start:
                            truncated = True
                        for chunk in response.iter_content(CHUNK_SIZE):
                            if stop_check and stop_check():
                                raise DownloadStopped(name)
                            if not chunk:
                                continue
                            handle.write(chunk)
                            wrote_any = True
                            current += len(chunk)
                            if bar:
                                bar.add(len(chunk))
                            if callback:
                                now = time.monotonic()
                                elapsed = now - last_time
                                if elapsed >= 0.25:  # throttle to ~4 reports/sec
                                    callback(name, current, total, (current - last_bytes) / elapsed)
                                    last_time, last_bytes = now, current

                if bar:
                    bar.finish()
                break  # transfer finished; no more attempts needed
            except (requests.RequestException, OSError) as error:
                last_error = error
                if attempt == MAX_ATTEMPTS:
                    break
                if target.exists() and (not overwrite or truncated):
                    start = target.stat().st_size
                else:
                    start = 0
                delay = _backoff(attempt)
                self._log(f"    [dl] {error} -> retry in {delay:.1f}s "
                          f"(resuming at {human_bytes(start)})")
                time.sleep(delay)

        final_size = target.stat().st_size if target.exists() else 0
        if not wrote_any:
            if initial > 0 and final_size >= initial:
                return DownloadResult(target, ALREADY_COMPLETE)
            raise RuntimeError(f"Download failed for '{name}': {last_error}")

        return DownloadResult(target, RESUMED if initial > 0 else DOWNLOADED)

    # -- cleanup ----------------------------------------------------------- #
    def close(self) -> None:
        """Close the calling thread's session, if one was opened."""
        session = getattr(self._local, "session", None)
        if session is not None:
            session.close()
            self._local.session = None
