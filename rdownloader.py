#!/usr/bin/env python3
"""RDownloader - CLI and GUI entry point (also used by PyInstaller)."""

from rdownloader.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
