"""Tkinter user interface."""

from __future__ import annotations

import concurrent.futures as cf
import os
import queue
import re
import shutil
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import unquote, urlparse

from .cache import ScanCache
from .feed import FeedCache, matches_item, parse_feed
from .client import DownloadStopped, LoLRomsClient
from .config import (
    APP_NAME,
    APP_VERSION,
    BASE_URL,
    DEFAULT_TOKEN,
    FULL_SITE_EXTRA_ROOTS,
    Options,
)
from .state import STATE_FILE, DoneRegistry, GuiSettings
from .util import (
    CURRENT,
    NEW,
    SIZE_DIFF,
    UPDATE,
    ListingEntry,
    LocalFileIndex,
    app_dir,
    clean_url,
    compare_file,
    compare_stat,
    display_path_for,
    format_eta,
    format_speed,
    human_bytes,
    local_path_for,
    path_in,
)

# Event names exchanged between worker threads and the Tk event pump.
EV_LOG = "log"
EV_FILES = "files"
EV_START = "start"
EV_LIVE = "live"
EV_COMPLETE = "complete"
EV_FAILED = "failed"
EV_RETRYING = "retrying"
EV_STOPPED = "stopped"
EV_ERROR = "error"
EV_DONE = "done"
EV_COMPARE = "compared"
EV_COMPARE_PROGRESS = "compare_progress"
EV_RESUME = "resume_available"
EV_LOAD_PROGRESS = "load_progress"
EV_RSS = "rss"

MAX_RETRY_ROUNDS = 3
PENDING = "pending"
EXCLUDED = "excluded"
FOLDER = "folder"
FOLDER_PREFIX = "folder:"
CHECKED = "\u2611"      # ballot box with check
UNCHECKED = "\u2610"    # empty ballot box
PARTIAL = "\u25ea"      # partially selected folder


@dataclass(frozen=True, slots=True)
class DisplayFile:
    """One scanned file, ready to be shown in the tree."""

    url: str
    label: str              # Pending | Skipped (done)
    remote_date: date | None = None
    local_date: date | None = None
    update: str = CURRENT   # New | Update | Size differs | Current
    remote_size: int | None = None
    local_size: int | None = None
    local_path: Path | None = None


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title(APP_NAME)
        root.geometry("900x600")
        root.minsize(700, 450)

        self.files: list[str] = []            # downloadable URLs
        self.total_discovered = 0             # pending + already-done rows
        self.selected: set[str] = set()       # URLs ticked for download
        self.stop = False
        self.base_path = ""

        self.q: queue.Queue = queue.Queue()
        self._iid_by_name: dict[str, str] = {}
        self._iids_by_basename: dict[str, list[str]] = {}
        self._folders: dict[str, str] = {}            # folder key -> tree iid
        self._folder_totals: dict[str, int] = {}      # folder key -> recursive file count
        self._folder_done: dict[str, int] = {}        # folder key -> finished files
        self._folder_dates: dict[str, date] = {}      # folder key -> newest remote date
        self._folder_new: dict[str, int] = {}
        self._folder_updates: dict[str, int] = {}
        self._folder_sizes: dict[str, int] = {}
        self._folder_current: dict[str, int] = {}
        self._folder_selectable: dict[str, int] = {}
        self._folder_selected: dict[str, int] = {}
        self._file_folders: dict[str, list[str]] = {}  # file URL -> ancestor keys
        self._file_update: dict[str, str] = {}        # file URL -> comparison status
        self._file_remote_date: dict[str, date | None] = {}
        self._file_remote_size: dict[str, int | None] = {}
        self._file_local_path: dict[str, Path] = {}
        self.remote_stats = dict(new=0, update=0, size=0, current=0)
        self._anchor: str | None = None               # Shift+click range anchor
        self._scan_base_url = ""                      # URL the current tree was scanned from
        self._comparing = False
        self._sort_column = ""
        self._sort_reverse = False
        self._tree_order: list[tuple[str, str, int]] = []
        self._resume_state: dict | None = None
        self.cache_dir = app_dir() / "cache"
        self._migrate_legacy_caches()
        self.cache = ScanCache(self.cache_dir / "scans")
        self.feed_cache = FeedCache(self.cache_dir / "feed.json")
        self._speeds: dict[str, float] = {}

        # Session statistics
        self.stats = dict(done=0, skipped=0, failed=0, stopped=0)
        self.bytes_done = 0
        self.file_sizes: dict[str, int] = {}
        self.speed_ema = 0.0
        self.run_total = 0
        self.run_start = 0.0
        self.run_end = 0.0

        self.settings = GuiSettings(app_dir() / STATE_FILE)

        self._build()
        self._restore_settings()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll()

    # -- settings ---------------------------------------------------------- #
    def _restore_settings(self) -> None:
        url, output = self.settings.load()
        if url:
            self.url.insert(0, url)
        if output:
            self.out.insert(0, output)

    def _save_settings(self) -> None:
        self.settings.save(self.url.get(), self.out.get())

    def _on_close(self) -> None:
        self.stop = True
        self._save_settings()
        self.root.destroy()

    def _migrate_legacy_caches(self) -> None:
        """Move old scan_cache/feed_cache files into the visible cache folder."""
        base = app_dir()
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            old_scans = base / "scan_cache"
            new_scans = self.cache_dir / "scans"
            if old_scans.is_dir() and not new_scans.exists():
                shutil.move(str(old_scans), str(new_scans))
            old_feed = base / "feed_cache.json"
            new_feed = self.cache_dir / "feed.json"
            if old_feed.is_file() and not new_feed.exists():
                shutil.move(str(old_feed), str(new_feed))
        except OSError:
            pass

    # -- layout ------------------------------------------------------------ #
    def _build(self) -> None:
        # The whole layout sits inside a canvas so the window itself can scroll
        # when it is smaller than its contents. The file tree and the log keep
        # their own scrollbars.
        outer = ttk.Frame(self.root)
        outer.pack(fill="both", expand=True)
        self._window_scrollbar = ttk.Scrollbar(outer, orient="vertical")
        self._window_scrollbar.pack(side="right", fill="y")
        self.canvas = tk.Canvas(outer, highlightthickness=0, borderwidth=0,
                                yscrollcommand=self._window_scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self._window_scrollbar.configure(command=self.canvas.yview)

        self.content = ttk.Frame(self.canvas)
        self._content_window = self.canvas.create_window((0, 0), window=self.content,
                                                         anchor="nw")
        self.content.bind("<Configure>", self._sync_scrollregion)
        self.canvas.bind("<Configure>", self._sync_scrollregion)
        parent = self.content

        # Header: app name on the left, Help menu and About button on the right.
        header = ttk.Frame(parent, padding=(8, 8, 8, 0))
        header.pack(fill="x")
        ttk.Label(header, text=APP_NAME, font=("", 11, "bold")).pack(side="left")
        self.help_btn = ttk.Menubutton(header, text="Help \u25be")
        self.help_btn.pack(side="right")
        self._help_menu = self._build_help_menu()
        self.help_btn.configure(menu=self._help_menu)

        # Row 1: URL + Load
        top = ttk.Frame(parent, padding=(8, 4, 8, 2))
        top.pack(fill="x")
        ttk.Label(top, text="URL:").grid(row=0, column=0, sticky="w")
        self.url = ttk.Entry(top)
        self.url.grid(row=0, column=1, columnspan=3, sticky="we", padx=4)
        self.load_btn = ttk.Button(top, text="Load", command=self.load)
        self.load_btn.grid(row=0, column=4, padx=4)
        top.columnconfigure(1, weight=1)

        # Row 2: Output + Browse
        row = ttk.Frame(parent, padding=(8, 0))
        row.pack(fill="x")
        ttk.Label(row, text="Output:").grid(row=0, column=0, sticky="w")
        self.out = ttk.Entry(row)
        self.out.grid(row=0, column=1, columnspan=3, sticky="we", padx=4)
        ttk.Button(row, text="Browse\u2026", command=self.pick_out).grid(row=0, column=4, padx=4)
        row.columnconfigure(1, weight=1)

        # Row 2b: optional folder to compare against (defaults to Output)
        row = ttk.Frame(parent, padding=(8, 2))
        row.pack(fill="x")
        ttk.Label(row, text="Compare:").grid(row=0, column=0, sticky="w")
        self.compare_entry = ttk.Entry(row)
        self.compare_entry.grid(row=0, column=1, columnspan=3, sticky="we", padx=4)
        ttk.Button(row, text="Browse\u2026", command=self.pick_compare).grid(
            row=0, column=4, padx=4)
        self.compare_btn = ttk.Button(row, text="Compare", command=self.recompare_all)
        self.compare_btn.grid(row=0, column=5, padx=(4, 0))
        row.columnconfigure(1, weight=1)

        # Row 3: Options
        row = ttk.Frame(parent, padding=(8, 4))
        row.pack(fill="x")
        self.rec = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="Include subfolders", variable=self.rec).grid(
            row=0, column=0, sticky="w")
        ttk.Label(row, text="Workers:").grid(row=0, column=1, sticky="e", padx=(8, 2))
        self.workers = ttk.Spinbox(row, from_=1, to=16, width=4)
        self.workers.set(5)
        self.workers.grid(row=0, column=2)
        ttk.Label(row, text="Filter:").grid(row=0, column=3, sticky="e", padx=(8, 2))
        self.filt = ttk.Entry(row, width=14)
        self.filt.grid(row=0, column=4)

        # Row 4: size verification / skip
        row = ttk.Frame(parent, padding=(8, 0))
        row.pack(fill="x")
        self.skip = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="Skip files that already exist on disk (by size)",
                        variable=self.skip).grid(row=0, column=0, sticky="w")
        self.full_site = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="Entire site", variable=self.full_site).grid(
            row=0, column=1, sticky="w", padx=(24, 0))
        self.use_cache = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="Use cached scan", variable=self.use_cache).grid(
            row=0, column=2, sticky="w", padx=(24, 0))
        self.only_changes = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="Only changes", variable=self.only_changes,
                        command=self._toggle_change_filter).grid(
            row=0, column=3, sticky="w", padx=(24, 0))

        # Row 5: progress + status
        self.progress_frame = ttk.Frame(parent, padding=(8, 4))
        self.progress_frame.pack(fill="x")
        self.progressbar = ttk.Progressbar(self.progress_frame, mode="determinate", length=200)
        self.progressbar.configure(maximum=100, value=0)
        self.progressbar.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.status_label = ttk.Label(self.progress_frame, text="", anchor="w")
        self.status_label.pack(side="right")

        # Row 6: actions
        buttons = ttk.Frame(parent, padding=(8, 2))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Reset progress", command=self._reset_progress).pack(side="left")
        ttk.Button(buttons, text="Select All", command=self._select_all).pack(side="left", padx=4)
        ttk.Button(buttons, text="Deselect All", command=self._deselect_all).pack(side="left", padx=4)
        ttk.Button(buttons, text="Expand All", command=self._expand_all).pack(side="left", padx=(12, 4))
        ttk.Button(buttons, text="Collapse All", command=self._collapse_all).pack(side="left", padx=4)
        self.start_btn = ttk.Button(buttons, text="Download Selected", command=self.start)
        self.start_btn.pack(side="right")
        self.stop_btn = ttk.Button(buttons, text="Stop", command=self._request_stop,
                                   state="disabled")
        self.stop_btn.pack(side="right", padx=4)
        self.updates_btn = ttk.Button(buttons, text="Download Updates",
                                      command=self.start_updates)
        self.updates_btn.pack(side="right", padx=4)
        self.resume_btn = ttk.Button(buttons, text="Resume", command=self.resume_download,
                                     state="disabled")
        self.resume_btn.pack(side="right", padx=4)

        # Row 7: discovered files, grouped into collapsible folders
        frame = ttk.LabelFrame(parent, text="Files Found  (click to toggle, Shift+click for a range, "
                                            "click a folder to expand)",
                               padding=4)
        frame.pack(fill="both", expand=True, padx=8, pady=(4, 0))

        columns = ("sel", "size", "modified", "local", "update", "status")
        self.tree = ttk.Treeview(frame, columns=columns, show="tree headings", height=12,
                                 selectmode="browse")
        self.tree.heading("#0", text="File", command=lambda: self._sort_by("name"))
        self.tree.column("#0", width=260, minwidth=80, anchor="w", stretch=False)
        for column, text, width, anchor, stretch in (
            ("sel", "Sel", 44, "center", False),
            ("size", "Size", 78, "e", False),
            ("modified", "Modified", 86, "center", False),
            ("local", "Local", 86, "center", False),
            ("update", "Update", 140, "center", False),
            ("status", "Status", 132, "w", True),
        ):
            self.tree.heading(column, text=text,
                              command=lambda c=column: self._sort_by(c))
            self.tree.column(column, width=width, minwidth=36, anchor=anchor,
                             stretch=stretch)

        self.tree.tag_configure(PENDING, background="#dbeafe", foreground="#123a75")
        self.tree.tag_configure(EXCLUDED, background="#ffffff", foreground="#9ca3af")
        self.tree.tag_configure("downloading", foreground="#003d99", background="#bfdbfe")
        self.tree.tag_configure("done", foreground="#047857")
        self.tree.tag_configure("skipped", foreground="#6b7280")
        self.tree.tag_configure("failed", foreground="#b91c1c")

        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Button-3>", self._show_tree_menu)

        # Row 7.5: session statistics
        self.stats_label = ttk.Label(parent, anchor="w", padding=(10, 2), font=("", 9))
        self.stats_label.pack(fill="x")

        # Row 8: aggregate speed
        self.total_speed = ttk.Label(parent, anchor="w", padding=(10, 2),
                                     font=("", 10, "bold"))
        self.total_speed.pack(fill="x")

        # Row 9: log with its own scrollbar
        log_frame = ttk.Frame(parent, padding=(8, 2, 8, 6))
        log_frame.pack(fill="x")
        self.log = tk.Text(log_frame, height=4, state="disabled", wrap="word")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")
        self.log.bind("<Button-3>", self._show_log_menu)

        self._bind_window_scrolling()
        self._bind_shortcuts()

    # -- window scrolling --------------------------------------------------- #
    def _bind_window_scrolling(self) -> None:
        """Route wheel events on the background/controls to the window scroll."""
        skip = {self.tree, self.log}
        own_scrollers = {"TEntry", "TSpinbox", "TCombobox", "TScrollbar"}

        def walk(widget: tk.Misc) -> None:
            for child in widget.winfo_children():
                if child in skip:
                    continue
                if child.winfo_class() in own_scrollers:
                    continue
                child.bind("<MouseWheel>", self._on_mousewheel, add="+")
                walk(child)

        self.canvas.bind("<MouseWheel>", self._on_mousewheel, add="+")
        walk(self.content)

    def _sync_scrollregion(self, _event: tk.Event | None = None) -> None:
        width = self.canvas.winfo_width()
        height = max(self.canvas.winfo_height(), self.content.winfo_reqheight())
        self.canvas.itemconfigure(self._content_window, width=width, height=height)
        self.canvas.configure(scrollregion=(0, 0, width, height))

    def _on_mousewheel(self, event: tk.Event) -> str | None:
        """Scroll the window; the tree and log keep their own scrollbars."""
        if self.content.winfo_reqheight() <= self.canvas.winfo_height():
            return None
        delta = int(event.delta)
        steps = int(-delta / 120) if delta else 0
        if steps == 0:
            steps = -1 if delta > 0 else 1
        self.canvas.yview_scroll(steps, "units")
        return "break"

    # -- context menus ------------------------------------------------------ #
    def _copy_to_clipboard(self, text: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def _set_file_selected(self, iid: str, selected: bool) -> None:
        if not self.tree.exists(iid) or self._is_folder(iid):
            return
        if self._row_tag(iid) in ("done", "skipped", "downloading", "failed"):
            return
        changed = (iid in self.selected) != selected
        if selected:
            self.selected.add(iid)
            self.tree.item(iid, tags=(PENDING,))
            self.tree.set(iid, "status", "Pending")
        else:
            self.selected.discard(iid)
            self.tree.item(iid, tags=(EXCLUDED,))
            self.tree.set(iid, "status", "Not selected")
        self.tree.set(iid, "sel", CHECKED if selected else UNCHECKED)
        if changed:
            delta = 1 if selected else -1
            for key in self._file_folders.get(iid, ()):
                self._folder_selected[key] = max(0, self._folder_selected.get(key, 0) + delta)
                self._refresh_folder_check(key)

    def _refresh_folder_check(self, key: str) -> None:
        iid = self._folders.get(key)
        if iid is None:
            return
        selectable = self._folder_selectable.get(key, 0)
        selected = self._folder_selected.get(key, 0)
        if selectable and selected >= selectable:
            glyph = CHECKED
        elif selected:
            glyph = PARTIAL
        else:
            glyph = UNCHECKED
        self.tree.set(iid, "sel", glyph)

    def _retire_file(self, url: str) -> None:
        """Drop a finished file from the selectable/selected counters."""
        if url not in self.selected:
            return
        self.selected.discard(url)
        self.tree.set(url, "sel", UNCHECKED)
        for key in self._file_folders.get(url, ()):
            self._folder_selectable[key] = max(0, self._folder_selectable.get(key, 0) - 1)
            self._folder_selected[key] = max(0, self._folder_selected.get(key, 0) - 1)
            self._refresh_folder_check(key)

    def _toggle_file(self, iid: str) -> None:
        self._set_file_selected(iid, iid not in self.selected)

    def _descendant_files(self, iid: str):
        for child in self.tree.get_children(iid):
            if self._is_folder(child):
                yield from self._descendant_files(child)
            else:
                yield child

    def _set_folder_selected(self, iid: str, selected: bool) -> None:
        for url in list(self._descendant_files(iid)):
            self._set_file_selected(url, selected)

    def _show_tree_menu(self, event: tk.Event) -> str:
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.focus(iid)
            self.tree.selection_set(iid)
        menu = self._build_tree_menu(iid)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def _build_tree_menu(self, iid: str) -> tk.Menu:
        """Build the right-click menu for the row under the cursor."""
        menu = tk.Menu(self.root, tearoff=0)

        if iid and self._is_folder(iid):
            is_open = bool(self.tree.item(iid, "open"))
            menu.add_command(label="Collapse folder" if is_open else "Expand folder",
                             command=lambda: self._toggle_folder(iid))
            menu.add_command(label="Select folder contents",
                             command=lambda: self._set_folder_selected(iid, True))
            menu.add_command(label="Deselect folder contents",
                             command=lambda: self._set_folder_selected(iid, False))
            menu.add_command(label="Compare with local folder\u2026",
                             command=lambda: self._ask_compare_folder(iid))
            menu.add_command(label="Check RSS for new files here",
                             command=lambda: self._start_rss_check(iid))
            menu.add_separator()
        elif iid:
            if iid in self.selected:
                menu.add_command(label="Deselect this file",
                                 command=lambda: self._set_file_selected(iid, False))
            else:
                can_select = self._row_tag(iid) not in ("done", "skipped",
                                                        "downloading", "failed")
                menu.add_command(label="Select this file",
                                 command=lambda: self._set_file_selected(iid, True),
                                 state="normal" if can_select else "disabled")
            menu.add_command(label="Copy URL", command=lambda: self._copy_to_clipboard(iid),
                             accelerator="Ctrl+C")
            menu.add_command(label="Check RSS for this game",
                             command=lambda: self._start_rss_check("", iid))
            menu.add_separator()

        if not iid:
            menu.add_command(label="Compare all with local folder\u2026",
                             command=lambda: self._ask_compare_folder(""))
            menu.add_command(label="Check RSS for whole site",
                             command=lambda: self._start_rss_check(""))
            menu.add_separator()

        menu.add_command(label="Select All", command=self._select_all,
                         state="normal" if self.files else "disabled", accelerator="Ctrl+A")
        menu.add_command(label="Deselect All", command=self._deselect_all,
                         accelerator="Ctrl+Shift+A")
        menu.add_command(label="Select new + updates", command=self._select_changed,
                         accelerator="Ctrl+Shift+U")
        menu.add_separator()
        menu.add_command(label="Expand All", command=self._expand_all, accelerator="Ctrl+E")
        menu.add_command(label="Collapse All", command=self._collapse_all,
                         accelerator="Ctrl+Shift+E")
        menu.add_separator()
        can_download = bool(self.selected) and str(self.start_btn["state"]) != "disabled"
        menu.add_command(label="Download Selected", command=self.start,
                         state="normal" if can_download else "disabled", accelerator="Ctrl+D")
        menu.add_command(label="Download new + updates", command=self.start_updates,
                         state="normal" if str(self.start_btn["state"]) != "disabled" else "disabled",
                         accelerator="Ctrl+Shift+D")
        menu.add_command(label="Resume stopped download", command=self.resume_download,
                         state="normal" if (self._resume_state and
                                            str(self.start_btn["state"]) != "disabled")
                         else "disabled", accelerator="Ctrl+Shift+R")
        return menu

    def _show_log_menu(self, event: tk.Event) -> str:
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Copy", command=self._copy_log_selection)
        menu.add_command(label="Copy all", command=lambda: self._copy_to_clipboard(
            self.log.get("1.0", "end-1c")))
        menu.add_command(label="Select All", command=self._select_log_text)
        menu.add_separator()
        menu.add_command(label="Clear log", command=self._clear_log)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def _copy_log_selection(self) -> None:
        try:
            text = self.log.get("sel.first", "sel.last")
        except tk.TclError:
            text = self.log.get("1.0", "end-1c")
        self._copy_to_clipboard(text)

    def _select_log_text(self) -> None:
        self.log.tag_add("sel", "1.0", "end-1c")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    # -- sorting / filtering ------------------------------------------------#
    @staticmethod
    def _size_sort_value(text: str) -> float:
        match = re.search(r"([\d.]+)\s*(B|KB|MB|GB|TB)", text or "")
        if not match:
            return -1.0
        units = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}
        return float(match.group(1)) * units[match.group(2)]

    def _sort_key(self, iid: str, column: str):
        if column == "name":
            return self.tree.item(iid, "text").lower()
        if column == "size":
            return self._size_sort_value(self.tree.set(iid, "size"))
        if column in ("modified", "local"):
            return self.tree.set(iid, column) or ""
        if column == "update":
            order = {NEW: 0, UPDATE: 1, SIZE_DIFF: 2, CURRENT: 3}
            return order.get(self.tree.set(iid, "update"), 4)
        return self.tree.set(iid, column).lower()

    def _apply_sort(self) -> None:
        if not self._sort_column:
            return
        reverse = self._sort_reverse

        def sort_level(parent: str) -> None:
            children = list(self.tree.get_children(parent))
            folders = [child for child in children if self._is_folder(child)]
            files = [child for child in children if not self._is_folder(child)]
            key = lambda child: self._sort_key(child, self._sort_column)  # noqa: E731
            folders.sort(key=key, reverse=reverse)
            files.sort(key=key, reverse=reverse)
            for index, child in enumerate(folders + files):
                self.tree.move(child, parent, index)
                if self._is_folder(child):
                    sort_level(child)

        sort_level("")

    def _update_headings(self) -> None:
        labels = {"name": "File", "sel": "Sel", "size": "Size", "modified": "Modified",
                  "local": "Local", "update": "Update", "status": "Status"}
        for column, label in labels.items():
            suffix = ""
            if column == self._sort_column:
                suffix = " \u25bc" if self._sort_reverse else " \u25b2"
            self.tree.heading("#0" if column == "name" else column, text=label + suffix)

    def _sort_by(self, column: str) -> None:
        if column == self._sort_column:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = column
            self._sort_reverse = False
        self._apply_change_filter()
        self._update_headings()

    def _capture_tree_order(self) -> None:
        order: list[tuple[str, str, int]] = []

        def walk(parent: str) -> None:
            for index, iid in enumerate(self.tree.get_children(parent)):
                order.append((iid, parent, index))
                if self._is_folder(iid):
                    walk(iid)

        walk("")
        self._tree_order = order

    def _normalize_tree(self) -> None:
        for iid, parent, index in self._tree_order:
            if self.tree.exists(iid):
                self.tree.move(iid, parent, index)

    def _apply_change_filter(self) -> None:
        self._normalize_tree()
        if self._sort_column:
            self._apply_sort()
        self._capture_tree_order()
        if not self.only_changes.get():
            return

        hidden: set[str] = set()
        for url in self._iid_by_name.values():
            if self._file_update.get(url, CURRENT) == CURRENT:
                if self.tree.exists(url):
                    self.tree.detach(url)
                hidden.add(url)

        folder_files: dict[str, set[str]] = {}
        for url, keys in self._file_folders.items():
            for key in keys:
                folder_files.setdefault(key, set()).add(url)
        for key, iid in self._folders.items():
            files = folder_files.get(key)
            if files and files <= hidden and self.tree.exists(iid):
                self.tree.detach(iid)

    def _toggle_change_filter(self) -> None:
        self._apply_change_filter()
        self._append("Showing only new/updated files."
                     if self.only_changes.get() else "Showing all files.")

    # -- small UI helpers -------------------------------------------------- #
    def _row_tag(self, iid: str) -> str:
        tags = self.tree.item(iid, "tags")
        return tags[0] if tags else ""

    @staticmethod
    def _is_folder(iid: str) -> bool:
        return iid.startswith(FOLDER_PREFIX)

    def _toggle_folder(self, iid: str) -> None:
        try:
            self.tree.item(iid, open=not bool(self.tree.item(iid, "open")))
        except tk.TclError:
            pass

    def _expand_all(self) -> None:
        for iid in self._folders.values():
            self.tree.item(iid, open=True)

    def _collapse_all(self) -> None:
        for iid in self._folders.values():
            self.tree.item(iid, open=False)

    def _select_all(self) -> None:
        urls = self.files
        if self.only_changes.get():
            urls = [url for url in urls
                    if self._file_update.get(url) != CURRENT]
        for url in urls:
            self._set_file_selected(url, True)

    def _select_changed(self) -> None:
        """Tick every file marked New, Update or Size differs."""
        for url, state in self._file_update.items():
            if state in (NEW, UPDATE, SIZE_DIFF):
                self._set_file_selected(url, True)

    def _deselect_all(self) -> None:
        for url in self._iid_by_name.values():
            self._set_file_selected(url, False)

    def _on_tree_click(self, event: tk.Event) -> None:
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        shift = bool(event.state & 0x0001)
        control = bool(event.state & 0x0004)
        column = self.tree.identify_column(event.x)

        # Clicking the tick box column always toggles selection (never expands).
        if column == "#1":
            if self._is_folder(iid):
                selectable = self._folder_selectable.get(iid, 0)
                selected = self._folder_selected.get(iid, 0)
                self._set_folder_selected(iid, not (selectable and selected >= selectable))
            elif shift and self._anchor:
                self._select_file_range(self._anchor, iid,
                                        select=not control)
            else:
                self._toggle_file(iid)
            self._anchor = iid
            return

        if self._is_folder(iid):
            # Clicks on the +/- indicator are already handled by the treeview itself.
            if "indicator" in (self.tree.identify_element(event.x, event.y) or ""):
                self._anchor = iid
                return
            if shift and self._anchor:
                self._select_file_range(self._anchor, iid, select=not control)
                return
            self._toggle_folder(iid)
            self._anchor = iid
            return

        if shift and self._anchor:
            self._select_file_range(self._anchor, iid, select=not control)
            return

        if self._row_tag(iid) in ("done", "skipped", "downloading", "failed"):
            self._anchor = iid
            return
        self._toggle_file(iid)
        self._anchor = iid

    # -- range selection ---------------------------------------------------- #
    def _file_order(self) -> list[str]:
        """All file rows in tree order, independent of collapsed folders."""
        order: list[str] = []

        def walk(parent: str) -> None:
            for child in self.tree.get_children(parent):
                if self._is_folder(child):
                    walk(child)
                else:
                    order.append(child)

        walk("")
        return order

    def _select_file_range(self, anchor: str, target: str, select: bool = True) -> None:
        """Apply *select* to every file between two rows (folder rows = subtree)."""
        if not anchor or not target or anchor == target:
            return
        order = self._file_order()
        positions = {url: index for index, url in enumerate(order)}

        def bounds(iid: str) -> list[int]:
            if self._is_folder(iid):
                return [positions[url] for url in self._descendant_files(iid)
                        if url in positions]
            return [positions[iid]] if iid in positions else []

        anchor_bounds = bounds(anchor)
        target_bounds = bounds(target)
        if not anchor_bounds or not target_bounds:
            return
        low = min(min(anchor_bounds), min(target_bounds))
        high = max(max(anchor_bounds), max(target_bounds))
        for url in order[low:high + 1]:
            self._set_file_selected(url, select)

    def _invert_selection(self) -> None:
        for url in list(self._iid_by_name.values()):
            if self.tree.exists(url):
                self._set_file_selected(url, url not in self.selected)

    # -- keyboard shortcuts -------------------------------------------------- #
    def _focus_is_text_input(self) -> bool:
        widget = self.root.focus_get()
        return bool(widget) and widget.winfo_class() in {
            "TEntry", "TSpinbox", "TCombobox", "Entry", "Spinbox", "Text"}

    def _bind_shortcuts(self) -> None:
        bindings = {
            "<F5>": self._shortcut_load,
            "<Control-r>": self._shortcut_load,
            "<Control-d>": self._shortcut_download,
            "<Escape>": self._shortcut_stop,
            "<Control-a>": self._shortcut_select_all,
            "<Control-Shift-A>": self._shortcut_deselect_all,
            "<Control-Shift-I>": self._shortcut_invert,
            "<Control-Shift-U>": self._shortcut_select_changed,
            "<Control-Shift-D>": self._shortcut_download_updates,
            "<Control-Shift-R>": self._shortcut_resume,
            "<Control-e>": self._shortcut_expand_all,
            "<Control-Shift-E>": self._shortcut_collapse_all,
            "<Control-l>": self._shortcut_focus_url,
            "<Control-f>": self._shortcut_focus_filter,
            "<Control-o>": self._shortcut_browse,
            "<Control-c>": self._shortcut_copy_url,
            "<Control-q>": self._shortcut_quit,
            "<F1>": self._shortcut_help,
            "<F9>": self._shortcut_rss,
        }
        for sequence, handler in bindings.items():
            self.root.bind(sequence, handler)
        self.tree.bind("<space>", self._on_tree_space)
        self.tree.bind("<Delete>", self._on_tree_delete)

    def _shortcut_load(self, _event: tk.Event | None = None) -> str:
        self.load()
        return "break"

    def _shortcut_download(self, _event: tk.Event | None = None) -> str:
        self.start()
        return "break"

    def _shortcut_download_updates(self, _event: tk.Event | None = None) -> str:
        self.start_updates()
        return "break"

    def _shortcut_resume(self, _event: tk.Event | None = None) -> str:
        self.resume_download()
        return "break"

    def _shortcut_stop(self, _event: tk.Event | None = None) -> str:
        self._request_stop()
        return "break"

    def _shortcut_select_all(self, _event: tk.Event | None = None) -> str | None:
        if self._focus_is_text_input():
            return None
        self._select_all()
        return "break"

    def _shortcut_deselect_all(self, _event: tk.Event | None = None) -> str | None:
        if self._focus_is_text_input():
            return None
        self._deselect_all()
        return "break"

    def _shortcut_invert(self, _event: tk.Event | None = None) -> str:
        self._invert_selection()
        return "break"

    def _shortcut_select_changed(self, _event: tk.Event | None = None) -> str:
        self._select_changed()
        return "break"

    def _shortcut_expand_all(self, _event: tk.Event | None = None) -> str:
        self._expand_all()
        return "break"

    def _shortcut_collapse_all(self, _event: tk.Event | None = None) -> str:
        self._collapse_all()
        return "break"

    def _shortcut_focus_url(self, _event: tk.Event | None = None) -> str:
        self.url.focus_set()
        self.url.selection_range(0, "end")
        return "break"

    def _shortcut_focus_filter(self, _event: tk.Event | None = None) -> str:
        self.filt.focus_set()
        self.filt.selection_range(0, "end")
        return "break"

    def _shortcut_browse(self, _event: tk.Event | None = None) -> str:
        self.pick_out()
        return "break"

    def _shortcut_copy_url(self, _event: tk.Event | None = None) -> str | None:
        if self.root.focus_get() is not self.tree:
            return None
        iid = self.tree.focus()
        if iid and not self._is_folder(iid):
            self._copy_to_clipboard(iid)
            return "break"
        return None

    def _shortcut_quit(self, _event: tk.Event | None = None) -> str:
        self._on_close()
        return "break"

    def _shortcut_help(self, _event: tk.Event | None = None) -> str:
        self._show_shortcuts()
        return "break"

    def _shortcut_rss(self, _event: tk.Event | None = None) -> str:
        self._start_rss_check("")
        return "break"

    def _on_tree_space(self, _event: tk.Event | None = None) -> str:
        iid = self.tree.focus()
        if not iid:
            return "break"
        if self._is_folder(iid):
            selectable = self._folder_selectable.get(iid, 0)
            selected = self._folder_selected.get(iid, 0)
            self._set_folder_selected(iid, not (selectable and selected >= selectable))
        else:
            self._toggle_file(iid)
        self._anchor = iid
        return "break"

    def _on_tree_delete(self, _event: tk.Event | None = None) -> str:
        iid = self.tree.focus()
        if not iid:
            return "break"
        if self._is_folder(iid):
            self._set_folder_selected(iid, False)
        else:
            self._set_file_selected(iid, False)
        return "break"

    def _show_shortcuts(self) -> None:
        messagebox.showinfo(APP_NAME + " \u2014 shortcuts", "\n".join([
            "Mouse",
            "  Click a file                toggle its download selection",
            "  Ctrl+Click                  toggle a file",
            "  Shift+Click                 select the range from the last clicked row",
            "  Ctrl+Shift+Click            deselect the range",
            "  Click a folder / its +/-    expand or collapse",
            "  Right-click                 context menu",
            "",
            "Keyboard",
            "  F5 / Ctrl+R                 Load (scan the URL)",
            "  Ctrl+D                      Download selected files",
            "  Ctrl+Shift+D                Download new/updated files (overwrite)",
            "  Ctrl+Shift+R                Resume remaining files after Stop",
            "  Esc                         Stop the current scan/download",
            "  Ctrl+A                      Select all",
            "  Ctrl+Shift+A                Deselect all",
            "  Ctrl+Shift+I                Invert selection",
            "  Ctrl+Shift+U                Select new, updated and resized files",
            "  Ctrl+E                      Expand all folders",
            "  Ctrl+Shift+E                Collapse all folders",
            "  Space                       Toggle the focused row",
            "  Delete                      Deselect the focused file",
            "  Ctrl+C                      Copy the focused file's URL",
            "  Ctrl+L                      Focus the URL field",
            "  Ctrl+F                      Focus the filter field",
            "  Ctrl+O                      Choose the output folder",
            "  Ctrl+Q                      Quit",
            "  F1                          Show this help",
            "  F9                          Check the RSS feed for new files",
        ]))

    # -- about / help -------------------------------------------------------- #
    def _build_help_menu(self) -> tk.Menu:
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Shortcuts\u2026", command=self._show_shortcuts,
                         accelerator="F1")
        menu.add_command(label="About " + APP_NAME, command=self._show_about)
        menu.add_separator()
        menu.add_command(label="Open output folder", command=self._open_output_folder)
        menu.add_command(label="Open cache folder", command=self._open_cache_folder)
        menu.add_command(label="Clear scan cache", command=self._clear_scan_cache)
        menu.add_command(label="Clear RSS cache", command=self._clear_feed_cache)
        menu.add_separator()
        menu.add_command(label="Check RSS for new files\u2026",
                         command=lambda: self._start_rss_check(""))
        menu.add_separator()
        menu.add_command(label="Quit", command=self._on_close, accelerator="Ctrl+Q")
        return menu

    def _show_about(self) -> None:
        messagebox.showinfo(
            "About " + APP_NAME,
            f"{APP_NAME} {APP_VERSION}\n\n"
            "Recursive, resumable downloader for lolroms.com with a folder tree, "
            "parallel transfers and resume support.\n\n"
            "Built with Python 3 and Tkinter. Only runtime dependency: requests.\n\n"
            "Press F1 in the main window for the full shortcut list.\n\n"
            "Use responsibly: respect the site's terms and the copyright status "
            "of ROMs in your jurisdiction.",
        )

    def _open_output_folder(self) -> None:
        folder = Path(self.out.get().strip() or ".")
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        if not hasattr(os, "startfile"):
            messagebox.showinfo(APP_NAME, f"Output folder: {folder}")
            return
        try:
            os.startfile(folder)  # type: ignore[attr-defined]
        except OSError as error:
            messagebox.showerror(APP_NAME, f"Could not open {folder}:\n{error}")

    def _open_cache_folder(self) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        if not hasattr(os, "startfile"):
            messagebox.showinfo(APP_NAME, f"Cache folder: {self.cache_dir}")
            return
        try:
            os.startfile(self.cache_dir)  # type: ignore[attr-defined]
        except OSError as error:
            messagebox.showerror(APP_NAME, f"Could not open {self.cache_dir}:\n{error}")

    def pick_out(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.out.get() or ".")
        if chosen:
            self.out.delete(0, "end")
            self.out.insert(0, chosen)

    def _append(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _registry(self) -> DoneRegistry:
        return DoneRegistry(Path(self.out.get().strip() or "."))

    # -- options / state --------------------------------------------------- #
    def _opts(self) -> Options:
        return Options(
            output=Path(self.out.get().strip() or "."),
            recursive=self.rec.get(),
            workers=max(1, int(self.workers.get())),
            scan_workers=8,
            filter_exts=tuple(ext.strip() for ext in self.filt.get().split(",") if ext.strip()),
            resume=True,
            check_size=self.skip.get(),
            timeout=60,
            token=DEFAULT_TOKEN,
            list_only=False,
            live_bar=False,
            quiet=True,
            progress_cb=self._on_progress,
        )

    def _reset_progress(self) -> None:
        registry = self._registry()
        if registry.reset():
            self._append("Progress reset. All files will be re-downloaded.")
        else:
            messagebox.showinfo(APP_NAME, "No progress file found.")

    def pick_compare(self) -> None:
        chosen = filedialog.askdirectory(
            initialdir=self.compare_entry.get() or self.out.get() or ".")
        if chosen:
            self.compare_entry.delete(0, "end")
            self.compare_entry.insert(0, chosen)

    # -- comparison ---------------------------------------------------------#
    def _compare_local_path(self, url: str, base_url: str) -> Path:
        text = self.compare_entry.get().strip()
        if text:
            return path_in(Path(text), url, base_url)
        return Path(self.out.get().strip() or ".") / local_path_for(url, base_url)

    def _tree_folder_scope(self) -> str:
        for iid in self.tree.selection():
            if self._is_folder(iid):
                return iid
        focus = self.tree.focus()
        return focus if focus and self._is_folder(focus) else ""

    def recompare_all(self) -> None:
        """Compare the selected remote folder (or whole scan) with a local folder."""
        if self._comparing:
            messagebox.showinfo(APP_NAME, "A comparison is already running.")
            return
        if not self._iid_by_name:
            messagebox.showinfo(APP_NAME, "Load a scan first.")
            return
        base = self._scan_base_url or clean_url(self.url.get())
        compare_text = self.compare_entry.get().strip()
        output = self.out.get().strip() or "."
        target = compare_text or output

        folder_iid = self._tree_folder_scope() if compare_text else ""
        if folder_iid:
            urls = list(self._descendant_files(folder_iid))
            prefix = folder_iid[len(FOLDER_PREFIX):]
            scope = f"folder '{self.tree.item(folder_iid, 'text')}'"
        else:
            urls = list(self._iid_by_name.values())
            prefix = ""
            scope = "whole scan"
        if not urls:
            messagebox.showinfo(APP_NAME, "The selected folder contains no files.")
            return

        self._comparing = True
        self.compare_btn.configure(state="disabled")
        self._begin_compare(len(urls), target)
        self._append(f"Scope: {scope}, {len(urls):,} file(s).")
        threading.Thread(
            target=self._recompare_worker,
            args=(urls, base, compare_text, output, prefix,
                  {url: self._file_remote_date.get(url) for url in urls},
                  {url: self._file_remote_size.get(url) for url in urls}),
            daemon=True,
        ).start()

    def _recompare_worker(self, urls: list[str], base: str, compare_text: str,
                          output: str, prefix: str,
                          dates: dict[str, date | None],
                          sizes: dict[str, int | None]) -> None:
        results: list[tuple[str, str, date | None, int | None, int | None, str]] = []
        total = len(urls)
        step = max(1, total // 200)
        index = LocalFileIndex()
        self.q.put((EV_COMPARE_PROGRESS, (0, total)))
        for index_number, url in enumerate(urls, 1):
            if compare_text:
                relative = display_path_for(url, base)
                sub = relative[len(prefix) + 1:] if prefix else relative
                local_path = Path(compare_text).joinpath(*sub.split("/"))
            else:
                local_path = Path(output) / local_path_for(url, base)
            status, local_date, local_size = compare_stat(
                dates.get(url), sizes.get(url), index.stat(local_path))
            results.append((url, status, local_date, local_size,
                            sizes.get(url), str(local_path)))
            if index_number == total or index_number % step == 0:
                self.q.put((EV_COMPARE_PROGRESS, (index_number, total)))
        self.q.put((EV_COMPARE, {"results": results,
                                 "target": compare_text or output}))

    def _begin_compare(self, total: int, target: str) -> None:
        self.progressbar.stop()
        self.progressbar.configure(mode="determinate", maximum=max(1, total), value=0)
        self.status_label.configure(text=f"Comparing 0/{total:,}\u2026")
        self._append(f"Comparing {total:,} file(s) with {target} \u2026")

    def _on_compare_progress(self, done: int, total: int) -> None:
        self.progressbar.configure(maximum=max(1, total), value=done)
        percent = (done / total * 100) if total else 100.0
        self.status_label.configure(
            text=f"Comparing {done:,}/{total:,} ({percent:.0f}%)\u2026")

    def _on_load_progress(self, done: int, total: int, label: str) -> None:
        self.progressbar.stop()
        self.progressbar.configure(mode="determinate", maximum=max(1, total), value=done)
        percent = (done / total * 100) if total else 100.0
        self.status_label.configure(
            text=f"{label} {done:,}/{total:,} ({percent:.0f}%)\u2026")

    def _rebuild_folder_summaries(self) -> None:
        """Recalculate folder dates/update counts from the file rows."""
        self._folder_dates.clear()
        self._folder_new.clear()
        self._folder_updates.clear()
        self._folder_sizes.clear()
        self._folder_current.clear()
        stats = dict(new=0, update=0, size=0, current=0)

        for url in self._iid_by_name.values():
            update = self._file_update.get(url, CURRENT)
            remote_date = self._file_remote_date.get(url)
            if update == NEW:
                stats["new"] += 1
            elif update == UPDATE:
                stats["update"] += 1
            elif update == SIZE_DIFF:
                stats["size"] += 1
            else:
                stats["current"] += 1
            for key in self._file_folders.get(url, ()):
                if update == NEW:
                    self._folder_new[key] = self._folder_new.get(key, 0) + 1
                elif update == UPDATE:
                    self._folder_updates[key] = self._folder_updates.get(key, 0) + 1
                elif update == SIZE_DIFF:
                    self._folder_sizes[key] = self._folder_sizes.get(key, 0) + 1
                else:
                    self._folder_current[key] = self._folder_current.get(key, 0) + 1
                if remote_date is not None:
                    newest = self._folder_dates.get(key)
                    if newest is None or remote_date > newest:
                        self._folder_dates[key] = remote_date

        self.remote_stats = stats
        for key, iid in self._folders.items():
            self.tree.set(iid, "modified", self._folder_date_text(key))
            self.tree.set(iid, "update", self._folder_update_text(key))

    def _ask_compare_folder(self, folder_iid: str) -> None:
        """Pick a local folder and compare a remote folder (with size checks)."""
        if self._comparing:
            messagebox.showinfo(APP_NAME, "A comparison is already running.")
            return
        if not self._iid_by_name:
            messagebox.showinfo(APP_NAME, "Load a scan first.")
            return
        urls = list(self._descendant_files(folder_iid))
        if not urls:
            messagebox.showinfo(APP_NAME, "That remote folder contains no files.")
            return
        chosen = filedialog.askdirectory(
            title="Choose the local folder to compare with",
            initialdir=self.compare_entry.get() or self.out.get() or ".")
        if not chosen:
            return

        prefix = folder_iid[len(FOLDER_PREFIX):] if folder_iid else ""
        base = self._scan_base_url or clean_url(self.url.get())
        dates = {url: self._file_remote_date.get(url) for url in urls}
        opts = self._opts()
        self._comparing = True
        self.compare_btn.configure(state="disabled")
        self._begin_compare(len(urls), chosen)
        threading.Thread(
            target=self._compare_folder_worker,
            args=(chosen, urls, prefix, base, dates, opts),
            daemon=True,
        ).start()

    def _compare_folder_worker(self, local_dir: str, urls: list[str], prefix: str,
                               base: str, dates: dict[str, date | None],
                               opts: Options) -> None:
        client = LoLRomsClient(opts)
        results: list[tuple[str, str, date | None, int | None, int | None, str]] = []
        total = len(urls)
        step = max(1, total // 200)
        self.q.put((EV_COMPARE_PROGRESS, (0, total)))
        index = LocalFileIndex()
        local_paths: dict[str, Path] = {}
        local_infos = {}
        for url in urls:
            relative = display_path_for(url, base)
            sub = relative[len(prefix) + 1:] if prefix else relative
            local_path = Path(local_dir).joinpath(*sub.split("/"))
            local_paths[url] = local_path
            local_infos[url] = index.stat(local_path)

        def compare_one(url: str):
            local_path = local_paths[url]
            try:
                remote_size = client.remote_size(url)
            except Exception:  # noqa: BLE001 - size is optional
                remote_size = None
            status, local_date, local_size = compare_stat(
                dates.get(url), remote_size, local_infos[url])
            return url, status, local_date, local_size, remote_size, str(local_path)

        with cf.ThreadPoolExecutor(max_workers=opts.scan_workers) as pool:
            futures = {pool.submit(compare_one, url): url for url in urls}
            for index, future in enumerate(cf.as_completed(futures), 1):
                try:
                    results.append(future.result())
                except Exception as error:  # noqa: BLE001
                    self.q.put((EV_LOG, f"[compare] {futures[future]} failed: {error}"))
                if index == total or index % step == 0:
                    self.q.put((EV_COMPARE_PROGRESS, (index, total)))
                if index % 100 == 0:
                    self.q.put((EV_LOG, f"[compare] {index:,}/{total:,} file(s)\u2026"))

        self.q.put((EV_COMPARE, {"results": results, "target": local_dir}))

    def _apply_comparison(self, payload, notify: bool = True) -> None:  # noqa: ANN001
        if isinstance(payload, list):
            payload = {"results": payload, "target": ""}
        self._comparing = False
        self.compare_btn.configure(state="normal")
        if "error" in payload:
            self.status_label.configure(text="Comparison failed.")
            self._append(f"Comparison failed: {payload['error']}")
            return

        results = payload.get("results", [])
        target = payload.get("target", "")
        for url, status, local_date, local_size, remote_size, local_path in results:
            self._file_update[url] = status
            self._file_remote_size[url] = remote_size
            self._file_local_path[url] = Path(local_path)
            if self.tree.exists(url):
                self.tree.set(url, "local", local_date.isoformat() if local_date else "")
                self.tree.set(url, "update", status)
                if remote_size is not None:
                    self.tree.set(url, "size", human_bytes(remote_size))

        self._rebuild_folder_summaries()
        counts = {NEW: 0, UPDATE: 0, SIZE_DIFF: 0, CURRENT: 0}
        for _, status, *_ in results:
            counts[status] = counts.get(status, 0) + 1
        self._apply_change_filter()
        try:
            self.progressbar.configure(value=float(self.progressbar.cget("maximum")))
        except (tk.TclError, ValueError):
            pass
        summary = (f"Compared {len(results):,} file(s) with {target or 'local folder'}: "
                   f"{counts[NEW]:,} new, {counts[UPDATE]:,} update(s), "
                   f"{counts[SIZE_DIFF]:,} size differs, {counts[CURRENT]:,} current.")
        self.status_label.configure(text=summary)
        self._append(summary)
        changed = [row for row in results if row[1] != CURRENT]
        for row in changed[:20]:
            self._append(f"  {row[1]:<13} {display_path_for(row[0], self._scan_base_url)}")
        if len(changed) > 20:
            self._append(f"  \u2026 and {len(changed) - 20:,} more changed file(s).")
        if notify and changed:
            self._show_compare_results(summary, changed)

    def _show_compare_results(self, summary: str, changed) -> None:  # noqa: ANN001
        """Non-modal window listing everything a comparison found."""
        window = tk.Toplevel(self.root)
        window.title("Comparison results")
        window.geometry("720x360")
        window.minsize(560, 240)
        window.transient(self.root)
        ttk.Label(window, text=summary, padding=8, wraplength=680).pack(fill="x")

        buttons = ttk.Frame(window, padding=8)
        buttons.pack(side="bottom", fill="x")
        urls = [row[0] for row in changed]

        def select_changed() -> None:
            for url in urls:
                self._set_file_selected(url, True)
            window.destroy()

        ttk.Button(buttons, text="Select changed files", command=select_changed).pack(side="left")
        ttk.Button(buttons, text="Close", command=window.destroy).pack(side="right")

        frame = ttk.Frame(window, padding=(8, 0))
        frame.pack(fill="both", expand=True)
        text = tk.Text(frame, wrap="none", height=12)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        for row in changed[:1000]:
            text.insert("end", f"{row[1]:<13} {display_path_for(row[0], self._scan_base_url)}\n")
        if len(changed) > 1000:
            text.insert("end", f"\u2026 and {len(changed) - 1000:,} more\n")
        text.configure(state="disabled")

    def _clear_scan_cache(self) -> None:
        if not messagebox.askyesno(APP_NAME, "Delete all cached scan results?"):
            return
        removed = self.cache.clear()
        self._append(f"Removed {removed:,} cached scan file(s).")

    # -- RSS feed -----------------------------------------------------------#
    def _folder_path_prefix(self, folder_iid: str) -> str:
        base_path = unquote(urlparse(self._scan_base_url or BASE_URL).path).rstrip("/")
        relative = folder_iid[len(FOLDER_PREFIX):] if folder_iid else ""
        return unquote(base_path + ("/" + relative if relative else ""))

    def _start_rss_check(self, folder_iid: str = "", game_url: str = "") -> None:
        query = ""
        exact_url = game_url
        if game_url:
            filename = unquote(urlparse(game_url).path.rsplit("/", 1)[-1])
            query = filename.rsplit(".", 1)[0]
        prefix = self._folder_path_prefix(folder_iid)
        compare_folder = self.compare_entry.get().strip()
        registry = (DoneRegistry(Path(compare_folder)) if compare_folder
                    else self._registry())
        done = registry.read()
        opts = self._opts()
        self.status_label.configure(text="Checking RSS\u2026")
        self._append("Checking RSS feed \u2026")
        threading.Thread(target=self._rss_worker,
                         args=(opts, prefix, query, exact_url, done), daemon=True).start()

    def _rss_worker(self, opts: Options, prefix: str, query: str, exact_url: str,
                    done: set[str]) -> None:
        client = LoLRomsClient(opts)
        try:
            items = parse_feed(client.fetch_feed())
        except Exception as error:  # noqa: BLE001
            self.q.put((EV_RSS, {"error": str(error)}))
            return
        relevant = [item for item in items
                    if matches_item(item, prefix, query, exact_url)]
        new_links = self.feed_cache.mark(relevant)
        todo = [item for item in relevant
                if item.link in new_links and item.link not in done]
        self.q.put((EV_RSS, {"items": items, "relevant": relevant,
                             "new_links": new_links, "todo": todo}))

    def _handle_rss(self, payload) -> None:  # noqa: ANN001
        if "error" in payload:
            self.status_label.configure(text="RSS check failed.")
            self._append(f"RSS check failed: {payload['error']}")
            return
        relevant = payload["relevant"]
        todo = payload["todo"]
        matched = [item for item in todo if self.tree.exists(item.link)]
        missing = [item for item in todo if not self.tree.exists(item.link)]
        for item in matched:
            self._set_file_selected(item.link, True)
        summary = (f"RSS: {len(relevant):,} relevant item(s), "
                   f"{len(todo):,} new to download")
        if missing:
            summary += f", {len(missing):,} not in current scan"
        self._append(summary)
        for item in todo[:20]:
            self._append(f"  NEW  {item.title}")
        if len(todo) > 20:
            self._append(f"  \u2026 and {len(todo) - 20:,} more")
        if missing:
            self._append("  Reload without 'Use cached scan' to include items "
                         "that are not in the current scan.")
        if not todo:
            self._append("Nothing new for this selection since the last RSS check.")
        self.status_label.configure(text=summary)

    def _clear_feed_cache(self) -> None:
        removed = self.feed_cache.clear()
        self._append("RSS cache cleared." if removed else "RSS cache is already empty.")

    # -- loading ----------------------------------------------------------- #
    def load(self) -> None:
        if self.full_site.get():
            url = BASE_URL
        else:
            url = clean_url(self.url.get())
        if not url:
            messagebox.showwarning(APP_NAME, "Please enter a URL first.")
            return
        if self.full_site.get():
            self._append(f"Scanning the entire site from {BASE_URL} (including FLAC and DATs) "
                         "\u2026 this can take a while.")
        self._scan_base_url = url
        compare_text = self.compare_entry.get().strip()
        compare_dir = Path(compare_text) if compare_text else None
        use_cache = self.use_cache.get()
        extra_starts = FULL_SITE_EXTRA_ROOTS if self.full_site.get() else ()
        self.stop = False
        self.load_btn.configure(state="disabled")
        self.progressbar.stop()
        self.progressbar.configure(mode="indeterminate", maximum=100, value=0)
        self.progressbar.start(15)
        self.status_label.configure(text="Loading\u2026")
        self._append("Loading " + url + " \u2026")
        opts = self._opts()
        compare_folder = compare_text
        registry = (DoneRegistry(Path(compare_folder)) if compare_folder
                    else self._registry())
        self.base_path = urlparse(url).path.rstrip("/")
        threading.Thread(target=self._load_worker,
                         args=(url, use_cache, compare_dir, opts, registry,
                               extra_starts),
                         daemon=True).start()

    def _load_worker(self, url: str, use_cache: bool,
                     compare_dir: Path | None, opts: Options,
                     registry: DoneRegistry, extra_starts=()) -> None:
        client = LoLRomsClient(opts)
        client.opts.log_cb = lambda message: self.q.put((EV_LOG, message))
        client.start = url
        try:
            snapshot = self.cache.load(url) if use_cache else None
            if snapshot is not None:
                files = list(snapshot.entries)
                note = "" if snapshot.complete else " (partial scan)"
                self.q.put((EV_LOG, f"Loaded {len(files):,} file(s) from cached scan "
                                    f"({snapshot.scanned_at:%Y-%m-%d %H:%M}){note}."))
            else:
                if use_cache:
                    self.q.put((EV_LOG, "No cached scan for this URL; scanning \u2026"))
                files = client.crawl(url, stop_check=lambda: self.stop,
                                     scan_workers=opts.scan_workers,
                                     extra_starts=extra_starts)
                if files:
                    self.cache.save(url, files, complete=not self.stop)
                    suffix = "" if not self.stop else " (partial)"
                    self.q.put((EV_LOG, f"Cached scan of {len(files):,} file(s){suffix}."))

            if not files and not self.stop:
                files = [ListingEntry(url, os.path.basename(urlparse(url).path) or "download")]

            done_urls = registry.read()
            rows: list[DisplayFile] = []
            pending = 0
            skipped = 0
            index = LocalFileIndex()
            total = len(files)
            self.q.put((EV_LOAD_PROGRESS, (0, total, "Comparing local files")))
            for position, entry in enumerate(files, 1):
                done = entry.url in done_urls
                label = "Skipped (done)" if done else "Pending"
                if done:
                    skipped += 1
                else:
                    pending += 1
                local_path = (path_in(compare_dir, entry.url, url) if compare_dir
                              else client.target_path(entry.url))
                update, local_date, local_size = compare_stat(
                    entry.date, None, index.stat(local_path))
                rows.append(DisplayFile(entry.url, label, entry.date, local_date,
                                        update, None, local_size, local_path))
                if position == total or position % 200 == 0:
                    self.q.put((EV_LOAD_PROGRESS,
                                (position, total, "Comparing local files")))

            if skipped:
                self.q.put((EV_LOG, f"Skipping {skipped:,} already-downloaded file(s)."))

            self.files = [row.url for row in rows if row.label == "Pending"]
            self.selected = set(self.files)
            self.q.put((EV_FILES, rows))

            if self.stop:
                self.q.put((EV_DONE, "Stopped by user \u2014 scan aborted, "
                                     "showing files found so far."))
        except Exception as error:  # noqa: BLE001
            self.q.put((EV_ERROR, str(error)))

    def _request_stop(self) -> None:
        scanning = str(self.load_btn["state"]) == "disabled"
        downloading = str(self.start_btn["state"]) == "disabled"
        if not (scanning or downloading):
            self.stop = False
            self.status_label.configure(text="Nothing to stop.")
            return
        self.stop = True
        self.status_label.configure(
            text="Stopping\u2026 active transfers will abort within a second.")

    # -- downloading ------------------------------------------------------- #
    def _updated_urls(self) -> list[str]:
        """Files whose comparison status needs downloading (scoped to a focused folder)."""
        urls = [url for url in self.files
                if self._file_update.get(url) in (NEW, UPDATE, SIZE_DIFF)]
        folder_iid = self._tree_folder_scope()
        if not folder_iid:
            return urls
        wanted = set(self._descendant_files(folder_iid))
        return [url for url in urls if url in wanted]

    def start(self) -> None:
        output = self.out.get().strip()
        if not output:
            messagebox.showwarning(APP_NAME, "Please choose an output folder first (Browse\u2026).")
            return
        urls = [url for url in self.files if url in self.selected]
        if self.only_changes.get():
            urls = [url for url in urls
                    if self._file_update.get(url) != CURRENT]
        if not urls:
            messagebox.showinfo(APP_NAME, "No files selected. Click rows to select them.")
            return
        overwrite = {url for url in urls
                     if self._file_update.get(url) in (UPDATE, SIZE_DIFF)}
        self._start_download(urls, overwrite, None,
                             self._opts(), self._scan_base_url or clean_url(self.url.get()),
                             self._registry())

    def start_updates(self) -> None:
        """Download new/updated/resized files, replacing existing copies."""
        compare_text = self.compare_entry.get().strip()
        if not compare_text and not self.out.get().strip():
            messagebox.showwarning(APP_NAME, "Choose an output folder (or a compare "
                                             "folder) first.")
            return
        if str(self.start_btn["state"]) == "disabled":
            messagebox.showinfo(APP_NAME, "A download is already running.")
            return
        urls = self._updated_urls()
        if not urls:
            messagebox.showinfo(APP_NAME, "No new, updated or resized files were found. "
                                         "Run a comparison first.")
            return
        for url in urls:
            self._set_file_selected(url, True)
        overwrite = {url for url in urls
                     if self._file_update.get(url) in (UPDATE, SIZE_DIFF)}
        targets = {url: self._file_local_path.get(url) for url in urls}
        destination = f"the compared folder '{compare_text}'" if compare_text else "Output"
        scope = self._tree_folder_scope()
        scope_text = f" in folder '{self.tree.item(scope, 'text')}'" if scope else ""
        self._append(f"Downloading {len(urls):,} new/updated file(s){scope_text} into "
                     f"{destination}; {len(overwrite):,} will overwrite existing files.")
        registry = (DoneRegistry(Path(compare_text)) if compare_text
                    else self._registry())
        self._start_download(urls, overwrite, targets,
                             self._opts(), self._scan_base_url or clean_url(self.url.get()),
                             registry)

    def resume_download(self) -> None:
        """Continue files that were not downloaded before Stop was pressed."""
        if self._resume_state is None:
            messagebox.showinfo(APP_NAME, "Nothing to resume.")
            return
        if str(self.start_btn["state"]) == "disabled":
            messagebox.showinfo(APP_NAME, "A download is already running.")
            return
        state = self._resume_state
        urls = list(state.get("urls") or [])
        if not urls:
            messagebox.showinfo(APP_NAME, "Nothing to resume.")
            return
        targets = state.get("targets") or {}
        overwrite = set(state.get("overwrite") or set())
        compare_text = self.compare_entry.get().strip()
        registry = (DoneRegistry(Path(compare_text)) if compare_text
                    else self._registry())
        self._append(f"Resuming {len(urls):,} remaining file(s)\u2026")
        self._start_download(urls, overwrite, {url: targets.get(url) for url in urls},
                             self._opts(),
                             self._scan_base_url or clean_url(self.url.get()),
                             registry)

    def _start_download(self, urls: list[str], overwrite: set[str],
                        targets: dict[str, Path | None] | None,
                        opts: Options, base_url: str,
                        registry: DoneRegistry) -> None:
        self.stop = False
        self._resume_state = None
        self.resume_btn.configure(state="disabled")
        self._reset_run_stats()
        self.run_total = len(urls)
        self.run_start = time.monotonic()
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        threading.Thread(target=self._download_worker_safe,
                         args=(urls, overwrite, targets or {}, opts, base_url, registry),
                         daemon=True).start()

    def _download_worker_safe(self, urls: list[str], overwrite: set[str],
                              targets: dict[str, Path | None], opts: Options,
                              base_url: str, registry: DoneRegistry) -> None:
        try:
            self._download_worker(urls, overwrite, targets, opts, base_url, registry)
        except Exception as error:  # noqa: BLE001 - never leave the UI stuck
            self.q.put((EV_LOG, f"[download] worker failed: {error}"))
            self.q.put((EV_DONE, f"Download failed: {error}"))

    def _reset_run_stats(self) -> None:
        self.stats = dict(done=0, skipped=0, failed=0, stopped=0)
        self.bytes_done = 0
        self.file_sizes.clear()
        self.speed_ema = 0.0
        self.run_total = 0
        self.run_start = 0.0
        self.run_end = 0.0
        self._speeds.clear()

    def _download_worker(self, urls: list[str], overwrite: set[str],
                         targets: dict[str, Path | None], opts: Options,
                         base_url: str, registry: DoneRegistry) -> None:
        workers = opts.workers
        counter_lock = threading.Lock()
        ok = 0
        remaining = list(urls)
        started: set[str] = set()
        completed: set[str] = set()

        def download_one(url: str, failed: list[tuple[str, str]], final_round: bool) -> None:
            nonlocal ok
            name = os.path.basename(unquote(urlparse(url).path))
            if self.stop:
                self.q.put((EV_STOPPED, (url, name)))
                return

            client = LoLRomsClient(opts)
            client.start = base_url
            started.add(url)
            self.q.put((EV_START, url))
            try:
                result = client.download_file(url, stop_check=lambda: self.stop,
                                              overwrite=url in overwrite,
                                              target_override=targets.get(url))
            except DownloadStopped:
                self.q.put((EV_STOPPED, (url, name)))
            except Exception as error:  # noqa: BLE001
                if final_round:
                    failed.append((url, name))
                    self.q.put((EV_FAILED, (url, name, str(error))))
                else:
                    self.q.put((EV_RETRYING, (url, name, str(error))))
            else:
                with counter_lock:
                    ok += 1
                self.q.put((EV_COMPLETE, (url, name, result.status, result.size)))
                completed.add(url)
                registry.add(url)

        for round_number in range(1, MAX_RETRY_ROUNDS + 1):
            if self.stop or not remaining:
                break
            if round_number > 1:
                self.q.put((EV_LOG, f"Retry round {round_number}/{MAX_RETRY_ROUNDS}: "
                                    f"{len(remaining)} file(s) to retry\u2026"))
                time.sleep(2)

            failed: list[tuple[str, str]] = []
            final_round = round_number == MAX_RETRY_ROUNDS
            if workers == 1:
                for url in remaining:
                    if self.stop:
                        break
                    download_one(url, failed, final_round)
            else:
                with cf.ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = [executor.submit(download_one, url, failed, final_round)
                               for url in remaining]
                    cf.wait(futures)

            if not failed:
                remaining = []
                break
            remaining = [url for url, _ in failed]

        pending = [url for url in urls if url not in completed]
        if pending:
            failed_urls = set(remaining)
            restart = {url for url in pending
                       if url in overwrite and (url not in started or url in failed_urls)}
            self.q.put((EV_RESUME, {"urls": pending,
                                    "overwrite": restart,
                                    "targets": dict(targets)}))

        failed_total = 0 if self.stop else len(remaining)
        verb = "Stopped by user" if self.stop else "Finished"
        self.q.put((EV_DONE, f"{verb}: {ok} ok, {failed_total} failed."))

    def _on_progress(self, name: str, done: int, total: int | None, speed: float) -> None:
        self.q.put((EV_LIVE, (name, done, total, speed)))

    # -- event pump -------------------------------------------------------- #
    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                self._handle_event(kind, payload)
        except queue.Empty:
            pass
        except Exception as error:  # noqa: BLE001 - keep the UI alive
            try:
                self._append(f"[poll error] {error}")
            except Exception:  # noqa: BLE001
                pass
        self._refresh_stats()
        self.root.after(120, self._poll)

    def _handle_event(self, kind: str, payload) -> None:  # noqa: ANN001
        if kind == EV_LOG:
            self._append(payload)
        elif kind == EV_FILES:
            self._show_files(payload)
        elif kind == EV_START:
            if self.tree.exists(payload):
                self.tree.item(payload, tags=("downloading",))
                self.tree.set(payload, "status", "Downloading\u2026")
        elif kind == EV_LIVE:
            self._update_live(*payload)
        elif kind == EV_COMPLETE:
            self._mark_complete(*payload)
        elif kind == EV_FAILED:
            self._mark_failed(*payload)
        elif kind == EV_RETRYING:
            self._mark_retrying(*payload)
        elif kind == EV_STOPPED:
            self._mark_stopped(*payload)
        elif kind == EV_ERROR:
            self._show_error(payload)
        elif kind == EV_DONE:
            self._finish_run(payload)
        elif kind == EV_COMPARE:
            self._apply_comparison(payload)
        elif kind == EV_COMPARE_PROGRESS:
            self._on_compare_progress(*payload)
        elif kind == EV_RESUME:
            self._resume_state = payload
            self.resume_btn.configure(state="normal")
            self._append(f"{len(payload['urls']):,} file(s) were not downloaded; "
                         "press Resume to continue them.")
        elif kind == EV_LOAD_PROGRESS:
            self._on_load_progress(*payload)
        elif kind == EV_RSS:
            self._handle_rss(payload)

    def _show_files(self, display: list[DisplayFile]) -> None:
        self.tree.delete(*self.tree.get_children())
        self._iid_by_name.clear()
        self._iids_by_basename.clear()
        self._folders.clear()
        self._folder_totals.clear()
        self._folder_done.clear()
        self._folder_dates.clear()
        self._folder_new.clear()
        self._folder_updates.clear()
        self._folder_sizes.clear()
        self._folder_current.clear()
        self._folder_selectable.clear()
        self._folder_selected.clear()
        self._file_folders.clear()
        self._file_update.clear()
        self._file_remote_date.clear()
        self._file_remote_size.clear()
        self._file_local_path.clear()
        self._anchor = None
        self.remote_stats = dict(new=0, update=0, size=0, current=0)

        for row in display:
            url, label = row.url, row.label
            relative = display_path_for(url, self.base_path)
            parts = relative.split("/")
            parent = ""
            keys: list[str] = []
            prefix: list[str] = []
            for part in parts[:-1]:
                prefix.append(part)
                key = FOLDER_PREFIX + "/".join(prefix)
                keys.append(key)
                node = self._folders.get(key)
                if node is None:
                    node = self.tree.insert(parent, "end", iid=key, text=part,
                                            values=("", "", "", "", "", ""),
                                            tags=(FOLDER,), open=False)
                    self._folders[key] = node
                parent = node

            selectable = label == "Pending"
            tag = PENDING if selectable else "skipped"
            modified = row.remote_date.isoformat() if row.remote_date else ""
            local = row.local_date.isoformat() if row.local_date else ""
            self.tree.insert(parent, "end", iid=url, text=parts[-1],
                             values=(CHECKED if selectable else UNCHECKED, "?",
                                     modified, local, row.update, label),
                             tags=(tag,))
            self._iid_by_name[relative] = url
            self._iids_by_basename.setdefault(parts[-1], []).append(url)
            self._file_folders[url] = keys
            self._file_update[url] = row.update
            self._file_remote_date[url] = row.remote_date
            self._file_remote_size[url] = row.remote_size
            if row.local_path is not None:
                self._file_local_path[url] = row.local_path

            if row.update == NEW:
                self.remote_stats["new"] += 1
            elif row.update == UPDATE:
                self.remote_stats["update"] += 1
            elif row.update == SIZE_DIFF:
                self.remote_stats["size"] += 1
            else:
                self.remote_stats["current"] += 1

            for key in keys:
                self._folder_totals[key] = self._folder_totals.get(key, 0) + 1
                if selectable:
                    self._folder_selectable[key] = self._folder_selectable.get(key, 0) + 1
                    self._folder_selected[key] = self._folder_selected.get(key, 0) + 1
                else:
                    self._folder_done[key] = self._folder_done.get(key, 0) + 1
                if row.update == NEW:
                    self._folder_new[key] = self._folder_new.get(key, 0) + 1
                elif row.update == UPDATE:
                    self._folder_updates[key] = self._folder_updates.get(key, 0) + 1
                elif row.update == SIZE_DIFF:
                    self._folder_sizes[key] = self._folder_sizes.get(key, 0) + 1
                else:
                    self._folder_current[key] = self._folder_current.get(key, 0) + 1
                if row.remote_date is not None:
                    newest = self._folder_dates.get(key)
                    if newest is None or row.remote_date > newest:
                        self._folder_dates[key] = row.remote_date

        for key, iid in self._folders.items():
            self.tree.set(iid, "modified", self._folder_date_text(key))
            self.tree.set(iid, "update", self._folder_update_text(key))
            self._refresh_folder_check(key)
            self._refresh_folder_row(key, iid)

        self.total_discovered = len(display)
        self._update_status(self.total_discovered)
        self._reset_run_stats()
        self._save_settings()
        self.progressbar.stop()
        self.progressbar.configure(mode="determinate", maximum=100, value=0)
        self.load_btn.configure(state="normal")
        self._tree_order = []
        self._apply_change_filter()
        self._update_headings()
        self._resume_state = None
        self.resume_btn.configure(state="disabled")

    def _folder_date_text(self, key: str) -> str:
        newest = self._folder_dates.get(key)
        return newest.isoformat() if newest else ""

    def _folder_update_text(self, key: str) -> str:
        new = self._folder_new.get(key, 0)
        updates = self._folder_updates.get(key, 0)
        sizes = self._folder_sizes.get(key, 0)
        parts = []
        if new:
            parts.append(f"{new} new")
        if updates:
            parts.append(f"{updates} upd")
        if sizes:
            parts.append(f"{sizes} size")
        if parts:
            return ", ".join(parts)
        return CURRENT if self._folder_totals.get(key, 0) else ""

    def _refresh_folder_row(self, key: str, iid: str) -> None:
        total = self._folder_totals.get(key, 0)
        done = self._folder_done.get(key, 0)
        status = f"{done:,}/{total:,} done" if done else f"{total:,} file(s)"
        self.tree.set(iid, "status", status)

    def _count_finished(self, url: str) -> None:
        """Advance completion counters for every folder above *url*."""
        for key in self._file_folders.get(url, ()):
            self._folder_done[key] = self._folder_done.get(key, 0) + 1
            iid = self._folders.get(key)
            if iid is not None:
                self._refresh_folder_row(key, iid)

    def _update_live(self, name: str, done: int, total: int | None, speed: float) -> None:
        iid = self._find_iid(name)
        if iid:
            size_text = human_bytes(done) if total is None else f"{human_bytes(done)}/{human_bytes(total)}"
            speed_text = format_speed(speed)
            self.tree.set(iid, "size", size_text)
            self.tree.set(iid, "status",
                          f"Downloading\u2026 {speed_text}" if speed_text else "Downloading\u2026")
        self._speeds[name] = speed
        if total:
            self.file_sizes[name] = max(self.file_sizes.get(name, 0), total)
        total_bps = sum(self._speeds.values())
        self.speed_ema = (total_bps if not self.speed_ema
                          else 0.85 * self.speed_ema + 0.15 * total_bps)
        self._update_speed_label()

    def _mark_complete(self, url: str, name: str, status: str, size: int) -> None:
        self._retire_file(url)
        if self.tree.exists(url):
            tag = "done" if status in ("downloaded", "resumed", "complete") else "skipped"
            self.tree.item(url, tags=(tag,))
            self.tree.set(url, "status", f"\u2713 {status.capitalize()}")
        if status in ("downloaded", "resumed"):
            self.stats["done"] += 1
        else:
            self.stats["skipped"] += 1
        self.bytes_done += size
        self.file_sizes[name] = max(self.file_sizes.get(name, 0), size)
        self._count_finished(url)
        self._speeds.pop(name, None)
        self._update_speed_label()

    def _mark_failed(self, url: str, name: str, error: str) -> None:
        self._retire_file(url)
        if self.tree.exists(url):
            self.tree.item(url, tags=("failed",))
            self.tree.set(url, "status", "\u2717 FAILED")
        self.stats["failed"] += 1
        self._count_finished(url)
        self._speeds.pop(name, None)
        self._update_speed_label()

    def _mark_retrying(self, url: str, name: str, error: str) -> None:
        if self.tree.exists(url):
            self.tree.item(url, tags=(PENDING,))
            self.tree.set(url, "status", "\u21bb Retrying\u2026")
        self._speeds.pop(name, None)
        self._update_speed_label()

    def _mark_stopped(self, url: str, name: str) -> None:
        self._retire_file(url)
        if self.tree.exists(url):
            self.tree.item(url, tags=("skipped",))
            self.tree.set(url, "status", "\u23f9 Stopped")
        self.stats["stopped"] += 1
        self._count_finished(url)
        self._speeds.pop(name, None)
        self._update_speed_label()

    def _show_error(self, message: str) -> None:
        self.progressbar.stop()
        self.load_btn.configure(state="normal")
        self.status_label.configure(text="Error")
        messagebox.showerror(APP_NAME, message)
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")

    def _finish_run(self, message: str) -> None:
        self.progressbar.stop()
        self.load_btn.configure(state="normal")
        self.status_label.configure(text=message)
        self.run_end = time.monotonic()
        self._speeds.clear()
        self.total_speed.configure(text=message)
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")

    def _update_speed_label(self) -> None:
        total_bps = sum(self._speeds.values())
        self.total_speed.configure(
            text=f"Total: {total_bps / 1048576:.2f} MB/s   |   Active: {len(self._speeds)} file(s)")

    def _find_iid(self, display_name: str) -> str | None:
        iid = self._iid_by_name.get(display_name)
        if iid and self.tree.exists(iid):
            return iid
        # Progress callbacks carry the bare file name; different folders may share it.
        for candidate in self._iids_by_basename.get(os.path.basename(display_name), ()):
            if self.tree.exists(candidate):
                return candidate
        return None

    def _update_status(self, total_files: int) -> None:
        if total_files > 0:
            self.status_label.configure(text=f"{total_files:,} file(s) loaded")
        else:
            self.status_label.configure(text="")

    def _refresh_stats(self) -> None:
        """Update the statistics bar: counts, volume, speed and ETA."""
        stats = self.stats
        finished = stats["done"] + stats["skipped"] + stats["failed"] + stats["stopped"]
        remaining = max(0, self.run_total - finished) if self.run_total else len(self.selected)

        parts = [f"Scanned: {self.total_discovered}", f"Selected: {len(self.selected)}"]
        if self.total_discovered:
            parts += [f"New: {self.remote_stats['new']:,}",
                      f"Updates: {self.remote_stats['update']:,}",
                      f"Sizes: {self.remote_stats['size']:,}",
                      f"Current: {self.remote_stats['current']:,}"]
        if self.run_total:
            parts += [f"Done: {stats['done']}", f"Skipped: {stats['skipped']}"]
            if stats["failed"]:
                parts.append(f"Failed: {stats['failed']}")
            if stats["stopped"]:
                parts.append(f"Stopped: {stats['stopped']}")
            parts.append(f"Remaining: {remaining}")

        # Estimate total volume from known sizes; unknown files use the average.
        known = sum(self.file_sizes.values())
        active = len(self._speeds)
        unknown_files = max(0, remaining - active) if self.run_total else 0
        average_size = known / len(self.file_sizes) if self.file_sizes else 0
        estimated_total = known + unknown_files * average_size
        remaining_bytes = max(0, int(estimated_total) - self.bytes_done)

        volume = f"Volume: {human_bytes(self.bytes_done)}"
        if estimated_total > self.bytes_done:
            volume += f"/{human_bytes(int(estimated_total))}"
        parts.append(volume)

        speed = self.speed_ema
        if not speed and self.run_start:
            elapsed = (self.run_end or time.monotonic()) - self.run_start
            if elapsed > 1:
                speed = self.bytes_done / elapsed
        if speed > 0:
            parts.append(f"Speed: {format_speed(speed)}")
            if remaining_bytes > 0 and not self.run_end:
                parts.append(f"ETA: ~{format_eta(remaining_bytes / speed)}")

        self.stats_label.configure(text="   |   ".join(parts))


def run_gui() -> int:
    try:
        root = tk.Tk()
    except Exception as error:  # noqa: BLE001
        print(f"GUI unavailable (needs Python with Tk): {error}")
        return 1
    App(root)
    root.mainloop()
    return 0
