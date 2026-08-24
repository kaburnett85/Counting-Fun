"""Reading the foreground window on Windows.

Raw ctypes, no pywin32: every call needed is a handful of lines, and avoiding
the dependency keeps both the install and the resident footprint smaller.

The whole probe costs a fraction of a millisecond, which is why polling every
few seconds is cheaper than the event hooks that would otherwise be tempting.

This file contains no decisions -- it reports what the OS says and nothing
more. That is deliberate: it is the one part of the app that cannot be tested
off Windows, so it holds nothing worth testing.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

from ...config import Config
from ...core.models import Snapshot
from ...logging_setup import get as get_logger
from .idle_win import IdleProbe

log = get_logger("backends.capture")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MAX_TITLE = 512

user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]

#: Store apps run inside this host, so the foreground process is the same for
#: all of them. Without the special case below, every UWP app looks identical.
APP_FRAME_HOST = "applicationframehost.exe"
CORE_WINDOW_CLASS = "Windows.UI.Core.CoreWindow"


class WindowsCaptureBackend:
    supports_urls = True

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.idle = IdleProbe(cfg.idle)
        self._path_cache: dict[int, str] = {}

    def start(self) -> None:
        pass

    def stop(self) -> None:
        self._path_cache.clear()

    def describe(self) -> dict[str, str]:
        snapshot = self.capture()
        return {
            "backend": "windows",
            "foreground app": snapshot.exe_name or "(none)",
            "window title": snapshot.title or "(none)",
            "idle seconds": f"{snapshot.idle_ms / 1000:.0f}",
            "locked": str(snapshot.locked),
        }

    def capture(self) -> Snapshot:
        now, monotonic = time.time(), time.monotonic()
        idle_ms, locked, screensaver = self.idle.probe()

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            # Happens on the secure desktop (lock screen, UAC prompt).
            return Snapshot(
                ts=now, monotonic=monotonic, idle_ms=idle_ms,
                locked=locked or True, screensaver=screensaver, unavailable=True,
            )

        title = _window_title(hwnd)
        pid = _process_id(hwnd)
        exe_path = self._exe_path(pid) if pid else ""
        exe_name = exe_path.rsplit("\\", 1)[-1].lower() if exe_path else ""

        if exe_name == APP_FRAME_HOST:
            inner_pid = _uwp_inner_pid(hwnd)
            if inner_pid and inner_pid != pid:
                inner_path = self._exe_path(inner_pid)
                if inner_path:
                    exe_path = inner_path
                    exe_name = inner_path.rsplit("\\", 1)[-1].lower()

        return Snapshot(
            ts=now, monotonic=monotonic, exe_name=exe_name, exe_path=exe_path,
            title=title, hwnd=int(hwnd), idle_ms=idle_ms,
            locked=locked, screensaver=screensaver,
        )

    def _exe_path(self, pid: int) -> str:
        cached = self._path_cache.get(pid)
        if cached is not None:
            return cached
        path = _process_path(pid)
        # PIDs are reused, so this cache is bounded and cheap to rebuild.
        if len(self._path_cache) > 256:
            self._path_cache.clear()
        self._path_cache[pid] = path
        return path


def _window_title(hwnd) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(min(length + 1, MAX_TITLE))
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value


def _process_id(hwnd) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _process_path(pid: int) -> str:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(MAX_TITLE)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _uwp_inner_pid(hwnd) -> int:
    """Find the real process behind a Store app's frame host window."""
    found: list[int] = []

    def callback(child, _lparam):
        buffer = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(child, buffer, len(buffer))
        if buffer.value == CORE_WINDOW_CLASS:
            found.append(_process_id(child))
            return False  # stop enumerating
        return True

    try:
        user32.EnumChildWindows(hwnd, WNDENUMPROC(callback), 0)
    except OSError:
        return 0
    return found[0] if found else 0
