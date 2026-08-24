"""Asking Windows how long it has been since you touched the keyboard.

Three signals, each cheap:

  GetLastInputInfo            milliseconds since the last keyboard or mouse input
  OpenInputDesktop            fails while the workstation is locked
  SPI_GETSCREENSAVERRUNNING   screensaver state

Known limitation, documented rather than hidden: GetLastInputInfo does not see
input delivered to a process running at a higher integrity level than this one.
Long stretches inside an elevated app can therefore look idle. The tracker
compensates by treating a foreground-window change as evidence of activity.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

from ...config import IdleConfig
from ...logging_setup import get as get_logger

log = get_logger("backends.idle")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

DESKTOP_SWITCHDESKTOP = 0x0100
SPI_GETSCREENSAVERRUNNING = 0x0072
#: Probing the desktop is the most expensive of the three; do it sparingly.
LOCK_PROBE_INTERVAL_S = 5.0


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
user32.GetLastInputInfo.restype = wintypes.BOOL
kernel32.GetTickCount.restype = wintypes.DWORD
user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
user32.OpenInputDesktop.restype = wintypes.HANDLE
user32.CloseDesktop.argtypes = [wintypes.HANDLE]
user32.SystemParametersInfoW.argtypes = [
    wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT
]


class IdleProbe:
    def __init__(self, cfg: IdleConfig):
        self.cfg = cfg
        self._last_lock_probe = 0.0
        self._locked = False

    def probe(self) -> tuple[int, bool, bool]:
        return self.idle_ms(), self.is_locked(), self.screensaver_running()

    def idle_ms(self) -> int:
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return 0
        # Both values are 32-bit and wrap after ~49.7 days. Doing the
        # subtraction in 32-bit unsigned arithmetic handles the wrap correctly;
        # mixing in GetTickCount64 here would produce a bogus 49-day idle.
        elapsed = ctypes.c_uint32(kernel32.GetTickCount() - info.dwTime).value
        return int(elapsed)

    def is_locked(self) -> bool:
        if not self.cfg.treat_lock_as_idle:
            return False
        now = time.monotonic()
        if now - self._last_lock_probe < LOCK_PROBE_INTERVAL_S:
            return self._locked
        self._last_lock_probe = now
        handle = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
        if handle:
            user32.CloseDesktop(handle)
            self._locked = False
        else:
            self._locked = True
        return self._locked

    def screensaver_running(self) -> bool:
        if not self.cfg.treat_screensaver_as_idle:
            return False
        running = wintypes.BOOL(False)
        try:
            user32.SystemParametersInfoW(
                SPI_GETSCREENSAVERRUNNING, 0, ctypes.byref(running), 0
            )
        except OSError:
            return False
        return bool(running.value)
