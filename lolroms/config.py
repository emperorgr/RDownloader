"""Application-wide constants and runtime options."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

APP_NAME = "LoLRoms Downloader"
APP_VERSION = "1.0.0"
BASE_URL = "https://lolroms.com"
#: Sections that are not linked from the site's main listing but belong to it.
FULL_SITE_EXTRA_ROOTS = (BASE_URL.rstrip("/") + "/FLAC",
                         BASE_URL.rstrip("/") + "/DATs")

#: Default download destination. Mirrors the historical behaviour.
DEFAULT_OUTPUT = Path(r"D:\Downloads\LoLRomsDownloader")

#: Soft token that the site's own front-end script sends with each request.
DEFAULT_TOKEN = "TransparentBanana"
#: Requests are far more reliable if they are not obviously a bare script.
USER_AGENT = f"LoLRoms Downloader/{APP_VERSION} (+{BASE_URL})"

CHUNK_SIZE = 1024 * 1024          # 1 MiB streaming chunks
MAX_ATTEMPTS = 4                  # attempts per request / per file
LISTING_TIMEOUT = 20              # listings are tiny; fail fast
MAX_BACKOFF = 30.0                # seconds, cap for exponential back-off
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

#: ``callback(name, done_bytes, total_bytes_or_None, bytes_per_second)``
ProgressCallback = Callable[[str, int, "int | None", float], None]
#: ``callback(message)``
LogCallback = Callable[[str], None]


@dataclass(slots=True)
class Options:
    """Everything the download engine needs to know about a run."""

    output: Path = DEFAULT_OUTPUT
    recursive: bool = True
    workers: int = 1
    scan_workers: int = 4
    filter_exts: tuple[str, ...] = ()
    resume: bool = True
    check_size: bool = True
    timeout: float = 60.0
    token: str = DEFAULT_TOKEN
    list_only: bool = False
    live_bar: bool = True
    quiet: bool = False
    full_site: bool = False
    log_cb: LogCallback | None = None
    progress_cb: ProgressCallback | None = None

    def __post_init__(self) -> None:
        self.output = Path(self.output)
        self.workers = max(1, int(self.workers))
        self.scan_workers = max(1, int(self.scan_workers))
        self.filter_exts = tuple(ext.lower() for ext in self.filter_exts if ext)
        self.timeout = float(self.timeout)

    def ext_allowed(self, name: str) -> bool:
        """Whether *name* passes the optional extension filter."""
        if not self.filter_exts:
            return True
        lowered = name.lower()
        return any(lowered.endswith(ext) for ext in self.filter_exts)
