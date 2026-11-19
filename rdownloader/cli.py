"""Command-line interface."""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import sys
import time
from pathlib import Path

from . import __version__
from .client import DownloadResult, RDownloaderClient
from .config import (
    APP_NAME,
    BASE_URL,
    DEFAULT_OUTPUT,
    DEFAULT_TOKEN,
    FULL_SITE_EXTRA_ROOTS,
    Options,
)
from .util import ListingEntry, human_bytes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rdownloader",
        description=f"Robust recursive downloader for {BASE_URL}",
        epilog=("examples:\n"
                '  python rdownloader.py "https://source.invalid/Acorn/Archimedes"\n'
                "  python rdownloader.py URL -o D:\\\\roms -w 4 --filter .7z,.flac\n"
                "  python rdownloader.py URL --list\n"
                "  python rdownloader.py --gui\n"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("url", nargs="?",
                        help="start URL (a file or a category page); omit to launch the GUI")
    parser.add_argument("-o", "--output", default=str(DEFAULT_OUTPUT),
                        help="output directory (default: %(default)s)")
    parser.add_argument("-w", "--workers", type=int, default=4,
                        help="parallel downloads (default: %(default)s)")
    parser.add_argument("--scan-workers", type=int, default=4,
                        help="parallel listing requests while scanning (default: %(default)s)")
    parser.add_argument("--full-site", action="store_true",
                        help=f"scan all of {BASE_URL} instead of the given URL")
    parser.add_argument("--filter", dest="filter_exts", default="",
                        help="comma-separated extensions to keep, e.g. .7z,.flac (default: all)")
    parser.add_argument("--no-recursive", action="store_true",
                        help="do not follow subfolders")
    parser.add_argument("--no-resume", action="store_true",
                        help="do not resume partial files with HTTP Range")
    parser.add_argument("--no-check-size", action="store_true",
                        help="skip existing files without verifying their size")
    parser.add_argument("--timeout", type=int, default=60,
                        help="socket timeout in seconds (default: %(default)s)")
    parser.add_argument("--token", default=DEFAULT_TOKEN, help="site token (default: built-in)")
    parser.add_argument("--list", action="store_true",
                        help="only list the files that would be downloaded")
    parser.add_argument("--gui", action="store_true", help="launch the graphical interface")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return parser


def _report(index: int, total: int, url: str, result: DownloadResult) -> None:
    print(f"[{index}/{total}] {result.status:<11} "
          f"{human_bytes(result.size):>9}  {result.path.name}")


def _report_failure(index: int, total: int, url: str, error: Exception) -> None:
    print(f"[{index}/{total}] {'FAILED':<11} {'':>9}  {url}\n        -> {error}")


def _download(client: RDownloaderClient, url: str, index: int, total: int) -> bool:
    try:
        result = client.download_file(url)
    except Exception as error:  # noqa: BLE001 - one bad file must not stop the batch
        _report_failure(index, total, url, error)
        return False
    _report(index, total, url, result)
    return True


def run(opts: Options, start_url: str) -> int:
    client = RDownloaderClient(opts)
    client.start = start_url

    try:
        files = client.crawl(start_url, scan_workers=opts.scan_workers,
                             extra_starts=FULL_SITE_EXTRA_ROOTS if opts.full_site else ())
    except Exception as error:  # noqa: BLE001 - surface a clean message, not a traceback
        print(f"Failed to scan {start_url}: {error}", file=sys.stderr)
        return 1

    if not files:
        files = [ListingEntry(start_url, start_url.rsplit("/", 1)[-1], None)]

    if opts.list_only:
        print(f"\n{len(files)} file(s) under {start_url}")
        for entry in files:
            stamp = f"  {entry.date}" if entry.date else ""
            print(f"  {entry.url}{stamp}")
        return 0

    print(f"\nDownloading {len(files)} file(s) to '{opts.output}' "
          f"(workers={opts.workers}) ...\n")

    ok = failed = 0
    started = time.time()
    total = len(files)

    if opts.workers <= 1:
        for index, entry in enumerate(files, 1):
            if _download(client, entry.url, index, total):
                ok += 1
            else:
                failed += 1
    else:
        with cf.ThreadPoolExecutor(max_workers=opts.workers) as executor:
            futures = {executor.submit(client.download_file, entry.url): entry
                       for entry in files}
            for index, future in enumerate(cf.as_completed(futures), 1):
                entry = futures[future]
                try:
                    result = future.result()
                except Exception as error:  # noqa: BLE001
                    _report_failure(index, total, entry.url, error)
                    failed += 1
                else:
                    _report(index, total, entry.url, result)
                    ok += 1

    elapsed = time.time() - started
    print(f"\nFinished in {elapsed:.1f}s \u2014 {ok} ok, {failed} failed. Output: {opts.output}")
    return 0 if failed == 0 else 2


def options_from_args(args: argparse.Namespace) -> Options:
    return Options(
        output=Path(args.output),
        recursive=not args.no_recursive,
        workers=args.workers,
        scan_workers=args.scan_workers,
        filter_exts=tuple(ext.strip() for ext in args.filter_exts.split(",") if ext.strip()),
        resume=not args.no_resume,
        check_size=not args.no_check_size,
        timeout=args.timeout,
        token=args.token,
        list_only=args.list,
        full_site=args.full_site,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.gui or not (args.url or args.full_site):
        try:
            from .gui import run_gui
        except Exception as error:  # noqa: BLE001 - no Tk in this Python build
            print(f"GUI unavailable (needs Python with Tk): {error}")
            return 1
        return run_gui()

    opts = options_from_args(args)
    start_url = BASE_URL if args.full_site else args.url
    try:
        return run(opts, start_url)
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
