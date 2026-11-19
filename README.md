# RDownloader

**Version 1.0.0** · A recursive, resumable downloader for
the source site, with both a command-line interface and a Tkinter GUI.
Packaged as a single Windows executable.

This is a cleaned-up rebuild of the original single-file script: behaviour is preserved,
the code is split into focused modules, and the HTTP layer is thread-safe.

## Features

- Recursive crawl of category pages, including multi-part subfolders (`Disc 1/`, `Disc 2/`, ...)
- Full-site scan of every platform and category (`--full-site` or the *Entire site* checkbox),
  including the `FLAC` and `DATs` sections that are not linked from the main listing
- Collapsible tree with per-file and per-folder tick boxes (folder tick selects its subtree)
- Click any column header to sort (folders stay grouped first); all columns are resizable
- **Only changes** filter hides `Current` files and empty folders, leaving just what needs work
- Downloads no longer scroll/follow the active rows, so the tree keeps your position and
  folders stay collapsed unless you open them
- Listing dates are compared with local file dates: `New`, `Update` or `Current`, with
  per-folder summaries and site-wide totals
- Optional size verification for a chosen folder: `Size differs` when the remote and
  local byte counts disagree
- Comparison shows a live progress bar and a results window listing every changed file,
  with a button to tick them all
- Compare against any local folder, not just the download output; whole scan or one
  remote folder at a time
- Scan results are cached to disk and can be reloaded without hitting the site again
- RSS feed watcher with a local seen-items cache; filters by platform/folder/game and
  ignores files already recorded in `.rdownloader_done.cfg`
- One-click/one-key selection of just the new, updated and resized files
- Resume interrupted transfers with HTTP `Range` requests
- Automatic retries with exponential back-off and jitter (per request and per file)
- Skips files whose local size already matches the remote file; resumes them when smaller
- Parallel downloads (`--workers N`), one HTTP session per worker thread
- Extension filter (`--filter .7z,.flac`), list-only mode (`--list`)
- Live console progress bar and GUI per-file progress, aggregate speed, volume and ETA
- GUI groups files into collapsible folders (`+ 32x` / `- 32x`) with per-folder
  completion counts and Expand All / Collapse All
- Scrollable main window plus dedicated scrollbars for the file list and log
- Selected files are highlighted with a blue row background and `Not selected` when
  excluded; folders stay normal until their row is selected
- Help menu at the top right (shortcuts, About, cache management); F1 opens the shortcut reference
- Right-click menu on the file list: select/deselect a file, select/deselect folder
  contents, copy URL, Select All / Deselect All, Expand/Collapse All, Download Selected
- Shift+click range selection and keyboard shortcuts for every common action (F1)
- GUI remembers the last URL/output folder and tracks completed files per output folder

## Quick start (built executable)

```
dist\RDownloader.exe
```

Double-clicking the executable opens the GUI. It stores two small state files next to itself
and/or in the chosen output folder:

| File | Purpose |
| --- | --- |
| `.rdownloader_state.cfg` | Last URL and output folder (next to the exe) |
| `.rdownloader_done.cfg` | URLs already downloaded into that output folder (download ledger) |
| `cache\scans\*.json` | Cached file lists/dates for every scanned start URL |
| `cache\feed.json` | RSS links already reported, so later checks only show new items |

The `cache` folder sits next to the executable; use **Help → Open cache folder** to
inspect it. Older `scan_cache\` / `feed_cache.json` files are moved there automatically.

### File list columns

Click a column header to sort ascending/descending (folders stay grouped first); drag
the separators to resize any column. **Only changes** hides `Current` rows and folders
with no differences.

| Column | Meaning |
| --- | --- |
| Sel | Tick box. Click it to select/deselect; folder ticks select the whole subtree (◪ = partial) |
| Size | Remote size once checked, otherwise download progress |
| Modified | Date shown by the site's listing for that file; folder rows show the newest date below them |
| Local | Modification date of the matching local file |
| Update | `New` (no local file), `Update` (remote date is newer), `Size differs` (same date, different byte count), `Current`. Folder rows summarise their contents |
| Status | Pending / Not selected / Downloading / Downloaded / Skipped / Failed |

Check **Entire site** to scan from `https://source.invalid/` instead of the URL in the box.
The statistics line shows site-wide `New`, `Updates`, `Sizes` and `Current` counts. Use
**Select new + updates** in the right-click menu (or Ctrl+Shift+U) to tick everything
that needs downloading.

### Scan cache, local comparison and RSS

- **Use cached scan** (on by default) reloads the last cached result for the URL instead
  of rescanning. Uncheck it to refresh from the site. Clear caches from the Help menu.
- **Compare:** field selects the folder to compare against. Leave it empty to compare
  with the **Output** folder. If a remote folder row is focused/selected when you press
  **Compare**, only that folder is compared and its contents are mapped directly onto the
  compare folder; otherwise the whole scan is compared. **Compare** recalculates the
  Local/Update columns for the loaded scan without any network traffic.
- **Download Updates** (toolbar, right-click menu or Ctrl+Shift+D) downloads every
  `New`, `Update` and `Size differs` file, respecting the focused folder when one is
  selected. `Update`/`Size differs` files are re-downloaded from zero and overwrite the
  existing copy; when **Compare** is set, they are written to the exact compared paths.
  Normal **Download Selected** also overwrites files marked as updated or resized.
- **Resume** (toolbar, menu or Ctrl+Shift+R) appears after you press **Stop** during a
  download and continues only the files that were not finished; partial transfers resume
  with HTTP Range. Stop now aborts within a chunk and always returns the UI to an idle state.
- **Only changes** hides rows whose comparison is `Current`; Select All and Download
  Selected then act on the visible changed files only.
- To compare one remote folder with an existing collection, right-click the folder and
  choose **Compare with local folder…**. This maps that remote folder onto the chosen
  local folder and also asks the site for each file's size (HEAD requests), so
  `Size differs` is detected even when dates match. The progress bar and status line
  show `Comparing 123/500 (25%)…`; when finished, a **Comparison results** window lists
  every changed file and offers **Select changed files**, while the Local/Update columns
  and folder summaries update in place.
- **RSS:** right-click a folder for *Check RSS for new files here*, a file for
  *Check RSS for this game*, or empty space / **F9** for the whole feed. The feed is
  filtered to the selection, checked against the local feed cache, and files already in
  `.rdownloader_done.cfg` are skipped. New matching entries are listed in the log and ticked
  in the tree when they are part of the current scan.

## Command line

```
python rdownloader.py "https://source.invalid/Acorn/Archimedes"
python rdownloader.py URL -o D:\roms -w 4 --filter .7z,.flac
python rdownloader.py URL --list
python rdownloader.py --full-site --list --scan-workers 8
python rdownloader.py URL --no-recursive --no-resume --no-check-size
python rdownloader.py --gui
python rdownloader.py --version
```

Defaults: output `D:\Downloads\RDownloader`, 4 workers, recursive, resume on,
size check on, 60 s socket timeout. `--scan-workers` controls parallel listing
requests (default 4). `--full-site` ignores the URL and scans from
`https://source.invalid/`. Exit codes: `0` all files OK, `2` some failures,
`1` scan failure.

## Keyboard and mouse shortcuts

The GUI shows the same list via **F1**.

| Input | Action |
| --- | --- |
| Click a file | Toggle its download selection |
| Ctrl+Click | Toggle a file |
| Shift+Click | Select the range from the last clicked row (folders count as their subtree) |
| Ctrl+Shift+Click | Deselect that range |
| Click a folder / its `+`/`-` | Expand or collapse |
| Right-click | Context menu |
| F5 / Ctrl+R | Load (scan the URL) |
| Ctrl+D | Download selected files |
| Esc | Stop the current scan/download |
| Ctrl+A | Select all |
| Ctrl+Shift+A | Deselect all |
| Ctrl+Shift+I | Invert selection |
| Ctrl+Shift+U | Select all new, updated and resized files |
| Ctrl+Shift+D | Download new/updated/resized files (overwrite) |
| Ctrl+Shift+R | Resume files left after Stop |
| Ctrl+E | Expand all folders |
| Ctrl+Shift+E | Collapse all folders |
| Space | Toggle the focused row |
| Delete | Deselect the focused file |
| Ctrl+C | Copy the focused file's URL (when the file list has focus) |
| Ctrl+L | Focus the URL field |
| Ctrl+F | Focus the filter field |
| Ctrl+O | Choose the output folder |
| F9 | Check the RSS feed for new files |
| Ctrl+Q | Quit |
| F1 | Shortcut help |

## Project layout

```
rdownloader.py     entry point (CLI + GUI), used by PyInstaller
app_gui.py                compatibility launcher for the GUI
RDownloader/
  config.py               constants and the Options dataclass
  util.py                 names, sizes, link extraction, path mapping, progress bar
  client.py               RDownloaderClient: crawl, remote size, resumable download
  cli.py                  argparse front end
  gui.py                  Tkinter application
  state.py                persisted settings and completed-file registry
  cache.py                scan-result cache (one JSON file per start URL)
  feed.py                 RSS parsing, relevance matching and seen-item cache
  __main__.py             allows `python -m RDownloader`
tests/
  test_util.py            pure helper tests
  test_local_download.py  crawl/download tests against a local HTTP server
  test_gui_tree.py        folder grouping, collapse and selection behaviour
  test_cache.py           scan cache round-trip and corruption handling
  test_feed.py            RSS parsing, matching and seen-item cache
smoke_test.py             launches the built exe and verifies the GUI window appears
RDownloader.spec    PyInstaller one-file, windowed build
build.ps1                 reproducible build script
.python/                  project-local CPython + pip + PyInstaller (not committed)
```

## Repository files

| File | Purpose |
| --- | --- |
| `README.md` | Overview, usage, build and development notes |
| `LICENSE` | MIT license |
| `CHANGELOG.md` | Release history |
| `CONTRIBUTING.md` | Development setup, tests and PR checklist |
| `CODE_OF_CONDUCT.md` | Community expectations |
| `SECURITY.md` | How to report vulnerabilities |
| `pyproject.toml` | Package metadata and console entry point |
| `version_info.txt` | Windows version resource embedded in the exe |
| `.github/workflows/tests.yml` | CI: unit tests and PyInstaller build artifact |

## Build

The project ships with its own Python toolchain in `.python/` so the build does not depend
on a system-wide Python installation:

```powershell
.\.python\python.exe -m pip install -r requirements-dev.txt
.\build.ps1
```

Output: `dist\RDownloader.exe` (one file, no console window).

If `.python/` is missing, install CPython 3.13 for the current user and point `build.ps1`
at that interpreter, or create the toolchain with:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

## Development and tests

```powershell
.\.python\python.exe -m unittest discover -s tests -v
.\.python\python.exe rdownloader.py --help
.\.python\python.exe rdownloader.py --gui
.\.python\python.exe smoke_test.py        # verifies dist\RDownloader.exe opens its window
```

## How a run works

1. **Scan** — the start URL is fetched, `<nav>` links are ignored, and every content
   row is classified using the site's markup (rows with a date are files, the rest are
   folders), so versioned folder names such as `FBNeo 1.0.0.03` are crawled correctly.
   Folders are queued breadth-first when recursive mode is enabled; full-site scans
   fetch several listing pages in parallel. File rows carry the date published by the
   listing.
2. **Compare & plan** — each file is compared with its local copy: missing → `New`,
   remote date newer than the local modification date → `Update`, otherwise
   `Current`. Extension filters are applied; the GUI additionally hides URLs listed
   in the output folder's `.rdownloader_done.cfg` and lets you tick folders/files.
3. **Transfer** — each file is written to `<output>/<category>/<subfolders>/<file>`.
   Existing files are compared with the remote size: equal/larger is skipped, smaller is
   resumed from its current length. Failed requests retry with jittered back-off.
4. **Finish** — the CLI prints a one-line summary; the GUI shows counts, volume, speed and ETA.

## Notes

- Requests carry the site's soft token and a descriptive User-Agent.
  Respect the site's terms and the copyright status of ROMs in your jurisdiction.
- Integrity is inferred from byte size only; no checksums are verified.
- `--token` overrides the built-in token if the site changes it.

## Requirements

- Windows 10/11
- Python 3.11+ with Tk (3.13 recommended for development)
- `requests` (installed automatically via `requirements.txt`)

The repository includes a project-local Python toolchain in `.python/` for building and
testing without touching a system installation; it is excluded from version control.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Run the test suite before opening a pull request:

```powershell
.\.python\python.exe -m unittest discover -s tests -t . -v
```

## License

[MIT](LICENSE) © 2026 RDownloader contributors.

This project is not affiliated with the source site. Download only material you are legally
allowed to use.
