"""Small, dependency-free helpers: names, sizes, links and paths."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

_URL_RE = re.compile(r"""https?://[^\s"'<>]+""")
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_SKIP_SCHEMES = ("javascript:", "mailto:", "data:", "tel:")


# --------------------------------------------------------------------------- #
# formatting
# --------------------------------------------------------------------------- #
def human_bytes(size: float | int | None) -> str:
    """Format a byte count as ``1.23 MB`` (or ``?`` when unknown)."""
    if size is None:
        return "?"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{value:.0f} B"  # pragma: no cover - loop always returns


def format_speed(bytes_per_second: float | None) -> str:
    return f"{bytes_per_second / 1048576:.2f} MB/s" if bytes_per_second else ""


def format_eta(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


# --------------------------------------------------------------------------- #
# names and URLs
# --------------------------------------------------------------------------- #
def sanitize_filename(name: str) -> str:
    """Make *name* safe to use as a Windows file or folder name."""
    cleaned = _BAD_CHARS.sub("_", name).strip().strip(".")
    return cleaned or "download"


def clean_url(text: str) -> str:
    """Pull the first http(s) URL out of possibly pasted/quoted text."""
    match = _URL_RE.search(text)
    return match.group(0).rstrip(".,;)'") if match else text.strip()


class _LinkParser(HTMLParser):
    """Collect ``<a href>`` values, ignoring anything inside ``<nav>``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []
        self._nav_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag == "nav":
            self._nav_depth += 1
        elif tag == "a" and not self._nav_depth:
            href = dict(attrs).get("href")
            if href:
                self.hrefs.append(href.strip())

    def handle_endtag(self, tag: str) -> None:
        if tag == "nav" and self._nav_depth:
            self._nav_depth -= 1


class _RowParser(HTMLParser):
    """Parse the site's ``<div class="row">`` listing rows and their dates."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, str]] = []
        self._nav_depth = 0
        self._row: dict[str, str] | None = None
        self._in_link = False
        self._in_meta = False

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        attributes = dict(attrs)
        if tag == "nav":
            self._nav_depth += 1
            return
        if self._nav_depth:
            return
        classes = (attributes.get("class") or "").split()
        if tag == "div" and "row" in classes:
            self._row = {"href": "", "name": "", "meta": "", "meta_seen": "0"}
        elif self._row is not None:
            if tag == "a":
                self._in_link = True
                self._row["href"] = (attributes.get("href") or "").strip()
            elif tag == "span" and "file-meta" in classes:
                self._in_meta = True
                self._row["meta_seen"] = "1"

    def handle_data(self, data: str) -> None:
        if self._nav_depth or self._row is None:
            return
        if self._in_meta:
            self._row["meta"] += data
        elif self._in_link:
            self._row["name"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "nav":
            if self._nav_depth:
                self._nav_depth -= 1
            return
        if self._row is None:
            return
        if tag == "a":
            self._in_link = False
        elif tag == "span":
            self._in_meta = False
        elif tag == "div":
            self.rows.append(self._row)
            self._row = None


@dataclass(frozen=True, slots=True)
class ListingEntry:
    """One link from a listing page, optionally with the site's date."""

    url: str
    name: str
    date: date | None = None
    kind: str | None = None  # "file" / "dir" when known from the markup


def parse_listing_date(text: str) -> date | None:
    match = _DATE_RE.search(text or "")
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


NEW = "New"
UPDATE = "Update"
SIZE_DIFF = "Size differs"
CURRENT = "Current"


@dataclass(frozen=True, slots=True)
class LocalStat:
    """Size and modification time of an existing local file."""

    size: int
    mtime: float


class LocalFileIndex:
    """Read local folders once with os.scandir instead of one stat per file.

    Comparing thousands of remote files against a network share otherwise costs
    one round-trip per file; a directory listing costs one round-trip per folder.
    """

    def __init__(self) -> None:
        self._directories: dict[Path, dict[str, LocalStat]] = {}

    def stat(self, path: Path) -> LocalStat | None:
        directory = self._directories.get(path.parent)
        if directory is None:
            directory = {}
            try:
                with os.scandir(path.parent) as entries:
                    for entry in entries:
                        try:
                            if entry.is_file():
                                info = entry.stat()
                                directory[entry.name] = LocalStat(info.st_size,
                                                                 info.st_mtime)
                        except OSError:
                            continue
            except OSError:
                pass
            self._directories[path.parent] = directory
        return directory.get(path.name)


def compare_stat(remote: date | None, remote_size: int | None,
                 local: LocalStat | None) -> tuple[str, date | None, int | None]:
    """Compare remote metadata with an indexed local file (``None`` = missing)."""
    if local is None:
        return NEW, None, None
    local_date = date.fromtimestamp(local.mtime)
    if remote is not None and local_date < remote:
        return UPDATE, local_date, local.size
    if remote_size is not None and remote_size != local.size:
        return SIZE_DIFF, local_date, local.size
    return CURRENT, local_date, local.size


def compare_file(remote: date | None, remote_size: int | None,
                 local_path: Path) -> tuple[str, date | None, int | None]:
    """Compare remote metadata with a local file.

    Returns ``(status, local_date, local_size)``. A newer remote date wins over
    a size difference; a size difference is reported when dates are not newer.
    """
    try:
        info = local_path.stat()
    except OSError:
        return compare_stat(remote, remote_size, None)
    return compare_stat(remote, remote_size, LocalStat(info.st_size, info.st_mtime))


def compare_dates(remote: date | None, local_path: Path) -> tuple[str, date | None]:
    """Compare a remote listing date with the local file's modification date.

    Returns ``(status, local_date)`` where status is ``New`` (no local file),
    ``Update`` (remote is newer) or ``Current`` (local is same/newer/unknown).
    """
    status, local, _ = compare_file(remote, None, local_path)
    return status, local


def path_in(root: Path, file_url: str, base_url: str) -> Path:
    """Map a remote file below *base_url* into *root* mirroring the tree."""
    return Path(root, *_relative_parts(file_url, base_url))


def extract_listing(page_html: str, page_url: str) -> list[ListingEntry]:
    """Return listing rows (with dates when the site provides them).

    Falls back to plain link extraction for pages without ``div.row`` markup,
    such as an ad-hoc HTTP server or a future layout change.
    """
    parser = _RowParser()
    parser.feed(page_html)
    parser.close()

    entries: list[ListingEntry] = []
    if parser.rows:
        seen: set[str] = set()
        for row in parser.rows:
            href = row["href"]
            if not href or href.startswith("#") or href.lower().startswith(_SKIP_SCHEMES):
                continue
            absolute = urljoin(page_url, href)
            if urlparse(absolute).scheme not in ("http", "https") or absolute in seen:
                continue
            seen.add(absolute)
            name = row["name"].strip() or unquote(os.path.basename(urlparse(absolute).path))
            kind = "file" if row.get("meta_seen") == "1" else "dir"
            entries.append(ListingEntry(absolute, name, parse_listing_date(row["meta"]),
                                        kind))
        return entries

    return [ListingEntry(url, unquote(os.path.basename(urlparse(url).path)))
            for url in extract_links(page_html, page_url)]


def extract_links(page_html: str, page_url: str) -> list[str]:
    """Return de-duplicated absolute http(s) links from a listing page."""
    parser = _LinkParser()
    parser.feed(page_html)
    parser.close()

    seen: set[str] = set()
    links: list[str] = []
    for href in parser.hrefs:
        if not href or href.startswith("#") or href.lower().startswith(_SKIP_SCHEMES):
            continue
        absolute = urljoin(page_url, href)
        if urlparse(absolute).scheme not in ("http", "https"):
            continue
        if absolute not in seen:
            seen.add(absolute)
            links.append(absolute)
    return links


@dataclass(frozen=True, slots=True)
class Link:
    kind: str  # "file" or "dir"
    url: str
    name: str


def classify_link(absolute_url: str, page_url: str) -> Link | None:
    """Classify a link as a child file/folder of *page_url*, else ``None``.

    Only links strictly *below* the current directory are returned, which
    automatically drops breadcrumbs, self-links and unrelated navigation.
    """
    path = urlparse(absolute_url).path.rstrip("/")
    current = urlparse(page_url).path.rstrip("/")
    if not path or path == current or not path.startswith(current + "/"):
        return None
    name = os.path.basename(path)
    kind = "file" if os.path.splitext(name)[1] else "dir"
    return Link(kind, absolute_url, name)


def classify_entry(entry: ListingEntry, page_url: str) -> Link | None:
    """Use the listing markup's file/folder kind, falling back to the extension."""
    child = classify_link(entry.url, page_url)
    if child is None:
        return None
    return Link(entry.kind or child.kind, child.url, child.name)


# --------------------------------------------------------------------------- #
# local path mapping
# --------------------------------------------------------------------------- #
def _relative_parts(file_url: str, base_url: str) -> list[str]:
    file_path = urlparse(file_url).path
    base_path = urlparse(base_url).path.rstrip("/")
    if file_path.startswith(base_path + "/"):
        relative = file_path[len(base_path) + 1:]
        return [sanitize_filename(unquote(part)) for part in relative.split("/") if part]
    return [sanitize_filename(unquote(os.path.basename(file_path)))]


def local_path_for(file_url: str, base_url: str) -> Path:
    """On-disk path (relative to the output folder) mirroring the site tree."""
    file_path = urlparse(file_url).path
    base_path = urlparse(base_url).path.rstrip("/")
    root_name = sanitize_filename(unquote(os.path.basename(base_path)) or "downloads")

    if file_path.startswith(base_path + "/"):
        return Path(root_name, *_relative_parts(file_url, base_url))
    # The URL *is* the file (single-file download) or lives elsewhere.
    return Path(sanitize_filename(unquote(os.path.basename(file_path))))


def display_path_for(file_url: str, base_url: str) -> str:
    """Folder-relative name shown in the GUI file list."""
    return "/".join(_relative_parts(file_url, base_url))


def app_dir() -> Path:
    """Folder holding the executable (frozen) or the project root (source)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# console progress
# --------------------------------------------------------------------------- #
class ProgressBar:
    """Minimal single-line, carriage-return progress bar."""

    def __init__(self, label: str, total: int | None) -> None:
        self.label = label
        self.total = total
        self.done = 0

    def add(self, amount: int) -> None:
        self.done += amount
        if self.total:
            percent = min(100.0, self.done / self.total * 100)
            message = (f"  {self.label[:46]:<46} "
                       f"{human_bytes(self.done)}/{human_bytes(self.total)} {percent:5.1f}%")
        else:
            message = f"  {self.label[:46]:<46} {human_bytes(self.done)}"
        try:
            print("\r" + message, end="", flush=True)
        except Exception:  # noqa: BLE001 - never break a download over a print
            pass

    def finish(self) -> None:
        try:
            print()
        except Exception:  # noqa: BLE001
            pass
