"""Persisted GUI state: window settings and the 'already downloaded' registry."""

from __future__ import annotations

import configparser
from pathlib import Path

STATE_FILE = ".lolroms_state.cfg"
DONE_FILE = ".lolroms_done.cfg"


class GuiSettings:
    """URL / output fields saved next to the executable between sessions."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> tuple[str, str]:
        parser = configparser.ConfigParser(interpolation=None)
        if not self.path.exists():
            return "", ""
        try:
            parser.read(self.path, encoding="utf-8")
            return (parser.get("settings", "url", fallback="").strip(),
                    parser.get("settings", "output", fallback="").strip())
        except (configparser.Error, OSError):
            return "", ""

    def save(self, url: str, output: str) -> None:
        parser = configparser.ConfigParser(interpolation=None)
        parser["settings"] = {"url": url.strip(), "output": output.strip()}
        try:
            with open(self.path, "w", encoding="utf-8") as handle:
                parser.write(handle)
        except OSError:
            pass


class DoneRegistry:
    """Tracks file URLs that were fully downloaded into a given folder."""

    def __init__(self, output_dir: Path) -> None:
        self.path = Path(output_dir) / DONE_FILE

    def read(self) -> set[str]:
        if not self.path.exists():
            return set()
        try:
            return {line.strip() for line in self.path.read_text(encoding="utf-8").splitlines()
                    if line.strip()}
        except OSError:
            return set()

    def add(self, url: str) -> None:
        try:
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(url + "\n")
        except OSError:
            pass

    def reset(self) -> bool:
        """Delete the registry. Returns ``True`` when a file was removed."""
        try:
            self.path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return False
