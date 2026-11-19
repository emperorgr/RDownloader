"""RSS feed parsing and a local 'seen items' cache."""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree

from .config import BASE_URL

RSS_URL = BASE_URL.rstrip("/") + "/rss.xml"
_AMP_RE = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)")
_ITEM_RE = re.compile(r"<item\b[^>]*>(.*?)</item>", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True, slots=True)
class FeedItem:
    title: str
    link: str
    published: datetime | None
    path: str  # site path, e.g. "SEGA/Mega Drive - Genesis/Game.7z"


def _make_item(title: str, link: str, raw_date: str, description: str) -> FeedItem | None:
    link = (link or "").strip()
    if not link:
        return None
    published: datetime | None = None
    raw_date = (raw_date or "").strip()
    if raw_date:
        try:
            published = parsedate_to_datetime(raw_date)
        except (TypeError, ValueError):
            published = None
    path = (description or "").strip()
    if not path:
        path = unquote(urlparse(link).path.lstrip("/"))
    return FeedItem((title or "").strip(), link, published, path)


def _parse_feed_regex(xml_text: str) -> list[FeedItem]:
    items: list[FeedItem] = []
    for block in _ITEM_RE.findall(xml_text):
        def field(name: str) -> str:
            match = re.search(rf"<{name}\b[^>]*>(.*?)</{name}>", block,
                              re.IGNORECASE | re.DOTALL)
            if not match:
                return ""
            value = match.group(1).strip()
            if value.startswith("<![CDATA[") and value.endswith("]]>"):
                value = value[9:-3]
            return html.unescape(value).strip()

        item = _make_item(field("title"), field("link"), field("pubDate"),
                          field("description"))
        if item is not None:
            items.append(item)
    return items


def parse_feed(xml_text: str) -> list[FeedItem]:
    """Parse the site's RSS feed.

    The live feed occasionally contains unescaped ``&`` characters, so a
    sanitised retry and a small regex fallback keep the check working.
    """
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        try:
            root = ElementTree.fromstring(_AMP_RE.sub("&amp;", xml_text))
        except ElementTree.ParseError:
            return _parse_feed_regex(xml_text)

    items: list[FeedItem] = []
    for node in root.iter("item"):
        item = _make_item(node.findtext("title") or "", node.findtext("link") or "",
                          node.findtext("pubDate") or "",
                          node.findtext("description") or "")
        if item is not None:
            items.append(item)
    return items


def matches_item(item: FeedItem, path_prefix: str = "", query: str = "",
                 exact_url: str = "") -> bool:
    """Whether a feed item belongs to a site folder, a game name or a URL."""
    if exact_url and item.link == exact_url:
        return True
    if path_prefix:
        prefix = path_prefix.strip("/").lower()
        path = item.path.strip("/").lower()
        if path != prefix and not path.startswith(prefix + "/"):
            return False
    if query:
        needle = query.lower()
        name = unquote(item.path.rsplit("/", 1)[-1]).lower()
        if needle not in item.title.lower() and needle not in name:
            return False
    return True


class FeedCache:
    """Remembers which RSS links have already been reported to the user."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        seen = payload.get("seen") if isinstance(payload, dict) else None
        if not isinstance(seen, dict):
            return {}
        return {str(key): str(value) for key, value in seen.items()}

    def mark(self, items: list[FeedItem]) -> set[str]:
        """Record *items* and return the links that were not seen before."""
        seen = self.load()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        new_links = {item.link for item in items if item.link not in seen}
        for item in items:
            seen.setdefault(item.link, now)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"version": 1, "seen": seen}, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass
        return new_links

    def clear(self) -> int:
        try:
            self.path.unlink()
            return 1
        except FileNotFoundError:
            return 0
        except OSError:
            return 0
