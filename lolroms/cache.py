"""Persistent cache of scan results, one JSON file per starting URL."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from .util import ListingEntry, parse_listing_date

CACHE_VERSION = 3


def normalize_cache_url(url: str) -> str:
    """Canonical cache key: no trailing slash, lowercase scheme/host."""
    parsed = urlparse((url or "").strip())
    if not parsed.scheme:
        return (url or "").strip().rstrip("/")
    path = parsed.path.rstrip("/")
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}{query}"


@dataclass(frozen=True, slots=True)
class ScanSnapshot:
    start_url: str
    scanned_at: datetime
    complete: bool
    entries: tuple[ListingEntry, ...]


class ScanCache:
    """Stores the file list (URL, name, date) of a scan on disk."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def path_for(self, start_url: str) -> Path:
        digest = hashlib.sha1(normalize_cache_url(start_url).encode("utf-8")).hexdigest()
        return self.directory / f"{digest}.json"

    def save(self, start_url: str, entries: list[ListingEntry],
             complete: bool = True) -> Path | None:
        canonical = normalize_cache_url(start_url)
        payload = {
            "version": CACHE_VERSION,
            "start_url": canonical,
            "scanned_at": datetime.now().isoformat(timespec="seconds"),
            "complete": bool(complete),
            "files": [
                {"url": entry.url, "name": entry.name,
                 "date": entry.date.isoformat() if entry.date else None,
                 "kind": entry.kind}
                for entry in entries
            ],
        }
        path = self.path_for(start_url)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
        except OSError:
            return None
        return path

    def load(self, start_url: str) -> ScanSnapshot | None:
        canonical = normalize_cache_url(start_url)
        path = self.path_for(canonical)
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict) or payload.get("version") != CACHE_VERSION:
            return None
        if normalize_cache_url(str(payload.get("start_url") or "")) != canonical:
            return None

        entries: list[ListingEntry] = []
        for item in payload.get("files") or []:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            kind = item.get("kind")
            if kind not in ("file", "dir"):
                kind = None
            entries.append(ListingEntry(
                str(item["url"]),
                str(item.get("name") or ""),
                parse_listing_date(str(item.get("date") or "")),
                kind,
            ))
        if not entries:
            return None
        try:
            scanned_at = datetime.fromisoformat(str(payload.get("scanned_at")))
        except ValueError:
            scanned_at = datetime.fromtimestamp(path.stat().st_mtime)
        return ScanSnapshot(start_url, scanned_at, bool(payload.get("complete", True)),
                            tuple(entries))

    def clear(self) -> int:
        """Delete every cached scan. Returns the number of files removed."""
        if not self.directory.exists():
            return 0
        removed = 0
        for path in self.directory.glob("*.json"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
        return removed
