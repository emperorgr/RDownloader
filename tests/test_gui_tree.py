"""GUI folder-tree tests (skipped when Tk is unavailable)."""

from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
import unittest
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from rdownloader.gui import (
    CHECKED,
    EV_COMPARE,
    EV_COMPLETE,
    EV_DONE,
    EV_RESUME,
    EV_STOPPED,
    PARTIAL,
    UNCHECKED,
    App,
    DisplayFile,
)
from rdownloader.state import STATE_FILE, GuiSettings

try:
    _probe = tk.Tk()
    _probe.destroy()
    TK_AVAILABLE = True
except Exception:  # noqa: BLE001 - headless machines or Python without Tk
    TK_AVAILABLE = False


@unittest.skipUnless(TK_AVAILABLE, "Tk is not available")
class FolderTreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.root)
        self.app.settings = GuiSettings(Path(self.tmp.name) / STATE_FILE)
        self.app.base_path = "/Arcade/FBNeo"
        base = "https://source.invalid/Arcade/FBNeo"
        self.base = base
        self.app._scan_base_url = base
        self.aburner = base + "/32x/aburner.7z"
        self.bracers = base + "/32x/bracers.7z"
        self.deep = base + "/32x/Sub/deep.7z"
        self.root_file = base + "/root.7z"
        self.old = base + "/32x/old.7z"

        self.app.files = [self.aburner, self.bracers, self.deep, self.root_file]
        self.app.selected = set(self.app.files)
        self.app._show_files([
            DisplayFile(self.aburner, "Pending", date(2026, 11, 14), None, "New"),
            DisplayFile(self.bracers, "Pending", date(2026, 11, 14), None, "New"),
            DisplayFile(self.deep, "Pending", date(2026, 11, 10), date(2026, 11, 10),
                        "Current"),
            DisplayFile(self.root_file, "Pending", date(2026, 11, 12), date(2026, 11, 1),
                        "Update"),
            DisplayFile(self.old, "Skipped (done)", date(2026, 11, 14), date(2026, 11, 14),
                        "New"),
        ])
        self.root.update_idletasks()

    def tearDown(self) -> None:
        self.root.destroy()

    def test_folders_are_grouped_and_collapsed(self) -> None:
        self.assertEqual(set(self.app._folders), {"folder:32x", "folder:32x/Sub"})
        self.assertEqual(self.app.tree.parent("folder:32x"), "")
        self.assertEqual(self.app.tree.parent("folder:32x/Sub"), "folder:32x")
        self.assertEqual(self.app.tree.parent(self.aburner), "folder:32x")
        self.assertEqual(self.app.tree.parent(self.root_file), "")
        self.assertIn(self.app.tree.item("folder:32x", "open"), (0, False))

    def test_child_rows_show_only_the_file_name(self) -> None:
        self.assertEqual(self.app.tree.item(self.aburner, "text"), "aburner.7z")
        self.assertEqual(self.app.tree.item("folder:32x", "text"), "32x")

    def test_folder_status_counts_nested_files_and_done_seed(self) -> None:
        # 4 files below 32x (incl. Sub/deep.7z); one was already downloaded.
        self.assertEqual(self.app.tree.set("folder:32x", "status"), "1/4 done")
        self.assertEqual(self.app.tree.set("folder:32x/Sub", "status"), "1 file(s)")

    def test_toggle_and_expand_collapse_all(self) -> None:
        self.app._toggle_folder("folder:32x")
        self.assertIn(self.app.tree.item("folder:32x", "open"), (1, True))
        self.app._collapse_all()
        self.assertTrue(all(self.app.tree.item(iid, "open") in (0, False)
                            for iid in self.app._folders.values()))
        self.app._expand_all()
        self.assertTrue(all(self.app.tree.item(iid, "open") in (1, True)
                            for iid in self.app._folders.values()))

    def test_selection_helpers_reach_files_inside_folders(self) -> None:
        self.app._deselect_all()
        self.assertEqual(self.app.selected, set())
        self.assertEqual(self.app._row_tag(self.bracers), "excluded")
        self.assertEqual(self.app._row_tag(self.old), "skipped")  # already done
        self.app._select_all()
        self.assertEqual(self.app.selected, set(self.app.files))
        self.assertEqual(self.app._row_tag(self.bracers), "pending")

    def test_completion_updates_folder_counters(self) -> None:
        self.app._mark_complete(self.aburner, "aburner.7z", "downloaded", 1234)
        self.assertEqual(self.app.stats["done"], 1)
        self.assertEqual(self.app.tree.set("folder:32x", "status"), "2/4 done")
        self.assertEqual(self.app.tree.set("folder:32x/Sub", "status"), "1 file(s)")

    def test_live_progress_finds_row_by_basename(self) -> None:
        self.assertEqual(self.app._find_iid("bracers.7z"), self.bracers)
        self.app._update_live("bracers.7z", 500, 1000, 2048.0)
        self.assertEqual(self.app.tree.set(self.bracers, "size"), "500 B/1000 B")
        self.assertIn("Downloading", self.app.tree.set(self.bracers, "status"))

    def test_window_and_log_have_scrollbars(self) -> None:
        self.assertTrue(self.app._window_scrollbar.winfo_exists())
        self.assertTrue(self.app.canvas.cget("yscrollcommand"))
        self.assertTrue(self.app.tree.cget("yscrollcommand"))
        self.assertTrue(self.app.log.cget("yscrollcommand"))
        self.assertTrue(self.app.tree.bind("<Button-3>"))
        self.assertTrue(self.app.log.bind("<Button-3>"))

    def test_folder_content_selection(self) -> None:
        self.app._set_folder_selected("folder:32x", False)
        self.assertEqual(self.app.selected, {self.root_file})
        self.assertEqual(self.app._row_tag(self.deep), "excluded")
        self.app._set_folder_selected("folder:32x", True)
        self.assertEqual(self.app.selected, set(self.app.files))

    def test_single_file_selection_helpers(self) -> None:
        self.app._set_file_selected(self.bracers, False)
        self.assertNotIn(self.bracers, self.app.selected)
        self.assertEqual(self.app._row_tag(self.bracers), "excluded")
        self.app._set_file_selected(self.bracers, True)
        self.assertIn(self.bracers, self.app.selected)
        # Finished rows cannot be re-selected.
        self.app._set_file_selected(self.old, True)
        self.assertNotIn(self.old, self.app.selected)

    @staticmethod
    def _menu_labels(menu: tk.Menu) -> list[str]:
        labels = []
        for index in range(menu.index("end") + 1):
            if menu.type(index) != "separator":
                labels.append(menu.entrycget(index, "label"))
        return labels

    @classmethod
    def _menu_entry(cls, menu: tk.Menu, label: str) -> int:
        for index in range(menu.index("end") + 1):
            if menu.type(index) != "separator" and menu.entrycget(index, "label") == label:
                return index
        raise AssertionError(f"menu entry {label!r} not found")

    def test_context_menu_for_file_row(self) -> None:
        menu = self.app._build_tree_menu(self.bracers)
        labels = self._menu_labels(menu)
        for expected in ("Deselect this file", "Copy URL", "Select All",
                         "Deselect All", "Download Selected"):
            self.assertIn(expected, labels)
        menu.destroy()

    def test_context_menu_for_finished_file_disables_select(self) -> None:
        menu = self.app._build_tree_menu(self.old)
        index = self._menu_entry(menu, "Select this file")
        self.assertEqual(menu.entrycget(index, "state"), "disabled")
        menu.destroy()

    def test_context_menu_disables_download_while_running(self) -> None:
        self.app.start_btn.configure(state="disabled")
        menu = self.app._build_tree_menu("")
        index = self._menu_entry(menu, "Download Selected")
        self.assertEqual(menu.entrycget(index, "state"), "disabled")
        menu.destroy()

    def test_context_menu_for_folder_and_empty_area(self) -> None:
        folder_menu = self.app._build_tree_menu("folder:32x")
        self.assertEqual(self._menu_labels(folder_menu), [
            "Expand folder", "Select folder contents", "Deselect folder contents",
            "Compare with local folder\u2026", "Check RSS for new files here",
            "Select All", "Deselect All", "Select new + updates",
            "Expand All", "Collapse All", "Download Selected",
            "Download new + updates", "Resume stopped download",
        ])
        folder_menu.destroy()

        empty_menu = self.app._build_tree_menu("")
        self.assertEqual(self._menu_labels(empty_menu), [
            "Compare all with local folder\u2026", "Check RSS for whole site",
            "Select All", "Deselect All", "Select new + updates",
            "Expand All", "Collapse All", "Download Selected",
            "Download new + updates", "Resume stopped download",
        ])
        empty_menu.destroy()

    def test_menu_accelerators_are_shown(self) -> None:
        menu = self.app._build_tree_menu("")
        for label, accelerator in (("Select All", "Ctrl+A"),
                                   ("Deselect All", "Ctrl+Shift+A"),
                                   ("Expand All", "Ctrl+E"),
                                   ("Collapse All", "Ctrl+Shift+E"),
                                   ("Download Selected", "Ctrl+D")):
            index = self._menu_entry(menu, label)
            self.assertEqual(menu.entrycget(index, "accelerator"), accelerator)
        menu.destroy()

    def test_shift_range_deselects_and_reselects(self) -> None:
        # setUp leaves everything selected.
        self.app._select_file_range(self.aburner, "folder:32x/Sub", select=False)
        self.assertEqual(self.app.selected, {self.root_file})
        self.assertEqual(self.app._row_tag(self.bracers), "excluded")
        self.assertEqual(self.app._row_tag(self.deep), "excluded")

        self.app._select_file_range(self.aburner, "folder:32x", select=True)
        self.assertEqual(self.app.selected, set(self.app.files))

    def test_shift_range_uses_tree_order(self) -> None:
        self.app._deselect_all()
        self.app._select_file_range(self.root_file, "folder:32x/Sub", select=True)
        # Sub/ and the root file are adjacent in tree order.
        self.assertEqual(self.app.selected, {self.deep, self.root_file})

    def test_shortcut_bindings_exist(self) -> None:
        global_sequences = ("<F5>", "<Control-r>", "<Control-d>", "<Escape>",
                            "<Control-a>", "<Control-Shift-A>", "<Control-Shift-I>",
                            "<Control-Shift-U>", "<Control-e>", "<Control-Shift-E>",
                            "<Control-l>", "<Control-f>", "<Control-o>", "<Control-c>",
                            "<Control-q>", "<F1>", "<F9>", "<Control-Shift-D>",
                            "<Control-Shift-R>")
        for sequence in global_sequences:
            self.assertTrue(self.app.root.bind(sequence), sequence)
        self.assertTrue(self.app.tree.bind("<space>"))
        self.assertTrue(self.app.tree.bind("<Delete>"))

    def test_shortcut_select_deselect_invert(self) -> None:
        self.app._focus_is_text_input = lambda: False  # type: ignore[assignment]
        self.app._shortcut_deselect_all()
        self.assertEqual(self.app.selected, set())
        self.app._shortcut_select_all()
        self.assertEqual(self.app.selected, set(self.app.files))
        self.app._shortcut_invert()
        self.assertEqual(self.app.selected, set())

    def test_space_toggles_focused_file(self) -> None:
        self.app.tree.focus(self.bracers)
        self.assertEqual(self.app._on_tree_space(), "break")
        self.assertNotIn(self.bracers, self.app.selected)
        self.assertEqual(self.app._anchor, self.bracers)

    def test_delete_deselects_focused_file(self) -> None:
        self.app.tree.focus(self.bracers)
        self.app._on_tree_delete()
        self.assertNotIn(self.bracers, self.app.selected)

    def test_selected_rows_use_blue_background(self) -> None:
        self.assertEqual(str(self.app.tree.tag_configure("pending", "background")), "#dbeafe")
        self.app._set_file_selected(self.bracers, False)
        self.assertEqual(str(self.app.tree.tag_configure("excluded", "background")), "#ffffff")
        self.assertEqual(self.app.tree.set(self.bracers, "status"), "Not selected")
        self.app._set_file_selected(self.bracers, True)
        self.assertEqual(self.app.tree.set(self.bracers, "status"), "Pending")

    def test_folders_have_no_colour_by_default(self) -> None:
        options = self.app.tree.tag_configure("folder")
        self.assertEqual(options.get("foreground"), "")
        self.assertEqual(options.get("background"), "")
        self.assertEqual(options.get("font"), "")

    def test_header_has_help_menu(self) -> None:
        self.assertTrue(self.app.help_btn.winfo_exists())
        self.assertFalse(hasattr(self.app, "about_btn"))
        self.assertEqual(self._menu_labels(self.app._help_menu), [
            "Shortcuts\u2026", "About RDownloader", "Open output folder",
            "Open cache folder", "Clear scan cache", "Clear RSS cache",
            "Check RSS for new files\u2026", "Quit",
        ])

    def test_checkbox_glyphs_and_folder_tick(self) -> None:
        self.assertEqual(self.app.tree.set(self.aburner, "sel"), CHECKED)
        self.assertEqual(self.app.tree.set("folder:32x", "sel"), CHECKED)

        self.app._set_file_selected(self.bracers, False)
        self.assertEqual(self.app.tree.set(self.bracers, "sel"), UNCHECKED)
        self.assertEqual(self.app.tree.set("folder:32x", "sel"), PARTIAL)

        self.app._set_folder_selected("folder:32x", False)
        self.assertEqual(self.app.tree.set("folder:32x", "sel"), UNCHECKED)
        for url in (self.aburner, self.bracers, self.deep):
            self.assertEqual(self.app.tree.set(url, "sel"), UNCHECKED)

        self.app._set_folder_selected("folder:32x", True)
        self.assertEqual(self.app.tree.set("folder:32x", "sel"), CHECKED)

    def test_dates_and_update_columns(self) -> None:
        self.assertEqual(self.app.tree.set(self.aburner, "modified"), "2026-11-14")
        self.assertEqual(self.app.tree.set(self.aburner, "local"), "")
        self.assertEqual(self.app.tree.set(self.deep, "local"), "2026-11-10")
        self.assertEqual(self.app.tree.set(self.aburner, "update"), "New")
        self.assertEqual(self.app.tree.set(self.deep, "update"), "Current")
        self.assertEqual(self.app.tree.set("folder:32x", "modified"), "2026-11-14")
        self.assertEqual(self.app.tree.set("folder:32x", "update"), "3 new")
        self.assertEqual(self.app.tree.set("folder:32x/Sub", "update"), "Current")

    def test_remote_difference_stats(self) -> None:
        self.assertEqual(self.app.remote_stats,
                         dict(new=3, update=1, size=0, current=1))
        self.app._refresh_stats()
        self.assertIn("New: 3", self.app.stats_label["text"])
        self.assertIn("Updates: 1", self.app.stats_label["text"])
        self.assertIn("Current: 1", self.app.stats_label["text"])

    def test_select_new_and_updates(self) -> None:
        self.app._deselect_all()
        self.app._select_changed()
        self.assertEqual(self.app.selected,
                         {self.aburner, self.bracers, self.root_file})
        self.assertEqual(self.app.tree.set("folder:32x", "sel"), PARTIAL)
        self.assertEqual(self.app.tree.set(self.root_file, "sel"), CHECKED)

    def test_entire_site_checkbox_exists(self) -> None:
        self.assertFalse(self.app.full_site.get())
        self.app.full_site.set(True)
        self.assertTrue(self.app.full_site.get())

    def test_updated_urls_and_button(self) -> None:
        self.assertEqual(set(self.app._updated_urls()),
                         {self.aburner, self.bracers, self.root_file})
        self.assertTrue(self.app.updates_btn.winfo_exists())

        self.app.tree.focus("folder:32x")
        self.assertEqual(set(self.app._updated_urls()), {self.aburner, self.bracers})

    def test_compare_uses_selected_folder_scope(self) -> None:
        self.app.compare_entry.insert(0, self.tmp.name)
        local = Path(self.tmp.name) / "Sub" / "deep.7z"
        local.parent.mkdir(parents=True)
        local.write_bytes(b"x")
        stamp = datetime(2030, 1, 1, 12).timestamp()
        os.utime(local, (stamp, stamp))
        self.app.tree.focus("folder:32x")

        self.app.recompare_all()
        payload = None
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                kind, item = self.app.q.get_nowait()
            except queue.Empty:
                time.sleep(0.02)
                continue
            if kind == EV_COMPARE:
                payload = item
                break
        self.assertIsNotNone(payload)
        self.app._apply_comparison(payload, notify=False)

        self.assertIn("Compared 4 file(s)", self.app.status_label["text"])
        self.assertEqual(self.app.tree.set(self.deep, "update"), "Current")
        self.assertEqual(self.app.tree.set(self.aburner, "update"), "New")

    def test_download_updates_uses_compared_paths(self) -> None:
        captured: dict[str, object] = {}

        def fake_start(urls, overwrite, targets, opts, base, registry):  # noqa: ANN001
            captured.update(urls=urls, overwrite=overwrite, targets=targets)

        self.app._start_download = fake_start  # type: ignore[assignment]
        self.app.compare_entry.insert(0, self.tmp.name)
        self.app._file_update[self.aburner] = "Update"
        local = Path(self.tmp.name) / "aburner.7z"
        self.app._file_local_path[self.aburner] = local
        self.app.tree.focus("folder:32x")

        self.app.start_updates()

        self.assertEqual(set(captured["urls"]), {self.aburner, self.bracers})
        self.assertIn(self.aburner, captured["overwrite"])
        self.assertEqual(captured["targets"][self.aburner], local)

    def test_download_worker_runs_without_tk_access(self) -> None:
        from unittest.mock import patch
        from rdownloader.client import DownloadResult
        from rdownloader.state import DoneRegistry

        class FakeClient:
            def __init__(self, opts) -> None:  # noqa: ANN001
                self.opts = opts

            def download_file(self, url, stop_check=None, overwrite=False,  # noqa: ANN001
                              target_override=None):
                return DownloadResult(fake_path, "downloaded")

        opts = self.app._opts()
        registry = DoneRegistry(Path(self.tmp.name))
        fake_path = Path(self.tmp.name) / "fake.bin"
        with patch("rdownloader.gui.RDownloaderClient", FakeClient):
            worker = threading.Thread(
                target=self.app._download_worker,
                args=([self.aburner], set(), {}, opts, self.base, registry),
                daemon=True,
            )
            worker.start()
            deadline = time.time() + 5
            done_message = None
            completed = False
            while time.time() < deadline:
                try:
                    kind, payload = self.app.q.get_nowait()
                except queue.Empty:
                    time.sleep(0.02)
                    continue
                if kind == EV_COMPLETE:
                    completed = True
                if kind == EV_DONE:
                    done_message = payload
                    break
        self.assertFalse(worker.is_alive())
        self.assertTrue(completed)
        self.assertIsNotNone(done_message)
        self.assertEqual(done_message, "Finished: 1 ok, 0 failed.")

    def test_stop_marks_pending_files_stopped(self) -> None:
        from rdownloader.state import DoneRegistry

        self.app.stop = True
        opts = self.app._opts()
        registry = DoneRegistry(Path(self.tmp.name))
        self.app._download_worker([self.aburner, self.bracers], set(), {},
                                  opts, self.base, registry)
        events = []
        while True:
            try:
                events.append(self.app.q.get_nowait())
            except queue.Empty:
                break
        self.assertFalse(any(kind == EV_COMPLETE for kind, _ in events))
        resume = [payload for kind, payload in events if kind == EV_RESUME][-1]
        self.assertEqual(set(resume["urls"]), {self.aburner, self.bracers})
        done = [payload for kind, payload in events if kind == EV_DONE][-1]
        self.assertTrue(done.startswith("Stopped by user"))

        self.app.start_btn.configure(state="normal")
        self.app.load_btn.configure(state="normal")
        self.app._request_stop()
        self.assertFalse(self.app.stop)
        self.assertIn("Nothing to stop", self.app.status_label["text"])

    def test_sort_columns_and_toggle(self) -> None:
        self.app._sort_by("name")
        self.assertEqual(self.app.tree.get_children(),
                         ("folder:32x", self.root_file))
        self.assertEqual(self.app.tree.get_children("folder:32x"),
                         ("folder:32x/Sub", self.aburner, self.bracers, self.old))
        self.assertIn("\u25b2", str(self.app.tree.heading("#0", "text")))

        self.app._sort_by("name")
        self.assertEqual(self.app.tree.get_children("folder:32x"),
                         ("folder:32x/Sub", self.old, self.bracers, self.aburner))
        self.assertIn("\u25bc", str(self.app.tree.heading("#0", "text")))

    def test_only_changes_filter_hides_current_rows(self) -> None:
        self.app.only_changes.set(True)
        self.app._apply_change_filter()
        children = self.app.tree.get_children("folder:32x")
        self.assertNotIn(self.deep, children)
        self.assertNotIn("folder:32x/Sub", children)
        self.assertIn(self.aburner, children)
        self.assertIn(self.old, children)  # New, even though already downloaded

        self.app.only_changes.set(False)
        self.app._apply_change_filter()
        self.assertIn("folder:32x/Sub", self.app.tree.get_children("folder:32x"))
        self.assertIn(self.deep, self.app.tree.get_children("folder:32x/Sub"))

    def test_resume_download_uses_saved_state(self) -> None:
        captured: dict[str, object] = {}

        def fake_start(urls, overwrite, targets, opts, base, registry):  # noqa: ANN001
            captured.update(urls=urls, overwrite=overwrite, targets=targets)

        self.app._start_download = fake_start  # type: ignore[assignment]
        self.app.compare_entry.insert(0, self.tmp.name)
        self.app._resume_state = {
            "urls": [self.aburner, self.bracers],
            "overwrite": {self.bracers},
            "targets": {self.aburner: Path("x.7z"), self.bracers: Path("y.7z")},
        }
        self.app.resume_download()
        self.assertEqual(set(captured["urls"]), {self.aburner, self.bracers})
        self.assertEqual(captured["overwrite"], {self.bracers})

    def test_file_column_width_is_persistent(self) -> None:
        self.assertFalse(bool(self.app.tree.column("#0", "stretch")))
        self.app.tree.column("#0", width=400)
        self.assertEqual(int(float(self.app.tree.column("#0", "width"))), 400)

    def test_compare_progress_and_results(self) -> None:
        self.app._comparing = True
        self.app._on_compare_progress(1, 4)
        self.assertAlmostEqual(float(self.app.progressbar["value"]), 1.0, places=1)
        self.assertIn("1/4", self.app.status_label["text"])

        self.app._apply_comparison({
            "target": "D:/collection",
            "results": [(self.aburner, "Size differs", date(2026, 11, 14), 10, 20,
                         "D:/collection/aburner.7z")],
        }, notify=False)
        self.assertFalse(self.app._comparing)
        self.assertEqual(self.app.tree.set(self.aburner, "update"), "Size differs")
        self.assertIn("1 size differs", self.app.status_label["text"])
        self.assertEqual(str(self.app.compare_btn["state"]), "normal")

    def test_folder_path_prefix(self) -> None:
        self.app._scan_base_url = "https://source.invalid/Arcade/FBNeo"
        self.assertEqual(self.app._folder_path_prefix("folder:32x"),
                         "/Arcade/FBNeo/32x")
        self.assertEqual(self.app._folder_path_prefix(""), "/Arcade/FBNeo")

    def test_rss_check_marks_relevant_files(self) -> None:
        from rdownloader.feed import FeedItem

        self.app._set_file_selected(self.aburner, False)
        item = FeedItem("aburner", self.aburner, None,
                        "Arcade/FBNeo/32x/aburner.7z")
        missing = FeedItem("new", "https://source.invalid/Arcade/FBNeo/32x/new.7z", None,
                           "Arcade/FBNeo/32x/new.7z")
        self.app._handle_rss({
            "items": [item, missing],
            "relevant": [item, missing],
            "new_links": {item.link, missing.link},
            "todo": [item, missing],
        })
        self.assertIn(self.aburner, self.app.selected)
        self.assertEqual(self.app.tree.set(self.aburner, "sel"), CHECKED)
        self.assertIn("2 new to download", self.app.status_label["text"])
        self.assertIn("not in current scan", self.app.status_label["text"])

    def test_entire_site_load_uses_site_root(self) -> None:
        from unittest.mock import patch
        from rdownloader.config import BASE_URL, FULL_SITE_EXTRA_ROOTS

        seen: dict[str, object] = {}

        class FakeClient:
            def __init__(self, opts) -> None:  # noqa: ANN001
                self.opts = opts
                self.start = None

            def crawl(self, url, stop_check=None, scan_workers=4,  # noqa: ANN001
                      extra_starts=()):
                seen["url"] = url
                seen["scan_workers"] = scan_workers
                seen["extra_starts"] = tuple(extra_starts)
                return []

            def target_path(self, url):  # noqa: ANN001
                return Path(self.tmp.name) / "missing.bin"

        class ImmediateThread:
            def __init__(self, target=None, args=(), daemon=None, **kwargs):  # noqa: ANN001
                self._target = target
                self._args = args

            def start(self) -> None:
                self._target(*self._args)

        with patch("rdownloader.gui.RDownloaderClient", FakeClient), \
                patch("rdownloader.gui.threading.Thread", ImmediateThread):
            self.app.full_site.set(True)
            self.app.load()

        self.assertEqual(seen["url"], BASE_URL)
        self.assertEqual(seen["scan_workers"], 8)
        self.assertEqual(seen["extra_starts"], FULL_SITE_EXTRA_ROOTS)
        self.assertEqual(self.app._scan_base_url, BASE_URL)


if __name__ == "__main__":
    unittest.main()
