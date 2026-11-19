#!/usr/bin/env python3
"""LoLRoms Downloader - CLI and GUI entry point (also used by PyInstaller)."""

from lolroms.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
