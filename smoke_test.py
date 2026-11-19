#!/usr/bin/env python3
"""Smoke test: launch the built exe, confirm the GUI window appears, then close it.

Success = a visible top-level window titled 'RDownloader' exists, which
proves Tk and all bundled imports work (a crash would show a traceback dialog
or the process would exit).
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from pathlib import Path

EXE = Path(__file__).resolve().parent / "dist" / "RDownloader.exe"
WANT_TITLE = "RDownloader"
TIMEOUT = 45

user32 = ctypes.windll.user32
EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.POINTER(ctypes.c_ssize_t))


def visible_titles() -> list[str]:
    titles: list[str] = []

    def callback(hwnd, _):  # noqa: ANN001
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            if buffer.value:
                titles.append(buffer.value)
        return True

    user32.EnumWindows(EnumProc(callback), None)
    return titles


def main() -> int:
    if not EXE.exists():
        print(f"FAIL: executable not found: {EXE}")
        return 1

    process = subprocess.Popen([str(EXE)], cwd=str(EXE.parent))
    try:
        deadline = time.time() + TIMEOUT
        while time.time() < deadline:
            titles = visible_titles()
            if WANT_TITLE in titles:
                print(f"PASS: GUI window '{WANT_TITLE}' is up (pid {process.pid})")
                return 0
            if process.poll() is not None:
                print(f"FAIL: process exited early with code {process.returncode}")
                return 1
            time.sleep(1)

        crash = any("exception" in title.lower() or "error" in title.lower()
                    for title in visible_titles())
        print(f"FAIL: no GUI window within {TIMEOUT}s; crash dialog present: {crash}")
        return 1
    finally:
        if process.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                           capture_output=True, check=False)
        print("(process closed)")


if __name__ == "__main__":
    sys.exit(main())
