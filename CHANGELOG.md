# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-11-19

First public release.

### Added

- Recursive, resumable downloads with HTTP Range, retries and exponential back-off.
- Parallel transfers and parallel listing scans, with a per-thread HTTP session.
- Full-site scan covering every platform plus the hidden `FLAC` and `DATs` sections.
- Collapsible file tree with per-file and per-folder tick boxes.
- Local scan cache in a visible `cache/` folder, including migration from older paths.
- Date comparison (`New`, `Update`, `Current`) and optional size verification
  (`Size differs`), against the output folder or any existing local collection.
- Scoped folder comparison with a progress bar and a results window.
- "Only changes" filter and one-key selection of new/updated/resized files.
- "Download Updates" with overwrite, plus Resume after Stop for unfinished files.
- RSS feed watcher with a seen-items cache, folder/game filtering and download-ledger
  awareness.
- Sortable, resizable columns; adjustable File column width.
- Keyboard shortcuts and right-click actions for the common operations.
- Help menu with shortcuts, About and cache management.
- Built-in application log with its own scrollbar and context menu.
