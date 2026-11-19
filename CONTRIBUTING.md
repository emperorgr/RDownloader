# Contributing

Thanks for improving RDownloader.

## Development setup

The repository ships with a project-local Python toolchain in `.python/` (not committed).
If it is missing, create a virtual environment instead:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

With the local toolchain already present:

```powershell
.\.python\python.exe -m pip install -r requirements-dev.txt
```

## Running tests

```powershell
.\.python\python.exe -m unittest discover -s tests -t . -v
```

The GUI tests use Tkinter and are skipped automatically when Tk is unavailable.
Tests must pass on Windows with Python 3.13; CI runs the same command on
`windows-latest`.

## Building the executable

```powershell
.\build.ps1
```

Output: `dist\RDownloader.exe` (one-file, windowed). Do not commit `build/`,
`dist/`, `.python/`, or the local `cache/` folder.

## Code style

- Python 3.11+ syntax with `from __future__ import annotations`.
- Keep Tkinter access on the UI thread; worker threads communicate through
  `App.q` events only.
- Prefer small, focused modules and dataclasses over dictionaries.
- Add or update tests for behaviour changes.
- Keep user-facing strings and documentation in English.

## Pull requests

1. Fork the repository and create a topic branch.
2. Add tests that demonstrate the fix or feature.
3. Run the full test suite and, if you can, the GUI smoke test:
   ```powershell
   .\.python\python.exe smoke_test.py
   ```
4. Describe the change, the motivation, and any UI screenshots in the PR.

## Legal

This project is a downloader, not a ROM provider. Do not add links to copyrighted
content, and keep the existing disclaimer intact. Respect the source site's terms of use.
