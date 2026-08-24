"""Process-level courtesies: one instance, low priority, small footprint."""

from __future__ import annotations

import ctypes
import hashlib
import os
from ctypes import wintypes

from ...logging_setup import get as get_logger

log = get_logger("backends.proc")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

ERROR_ALREADY_EXISTS = 183
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000

kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]

_mutex_handle = None


def claim_single_instance() -> bool:
    """Two copies running would double-count every minute of the day."""
    global _mutex_handle
    identity = hashlib.sha1(
        (os.environ.get("USERNAME", "") + os.environ.get("USERDOMAIN", "")).encode()
    ).hexdigest()[:16]
    _mutex_handle = kernel32.CreateMutexW(None, True, f"Local\\TimeSplit-{identity}")
    if not _mutex_handle:
        return True  # cannot tell; do not block startup over it
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


def lower_priority() -> bool:
    """Below-normal priority: direct insurance against slowing the machine."""
    try:
        return bool(
            kernel32.SetPriorityClass(
                kernel32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS
            )
        )
    except OSError:
        return False


def trim_working_set() -> bool:
    """Hand pages back to the OS after a burst (opening the dashboard).

    Windows will page them back in if they are needed again; in the meantime
    the process stops looking like it is hoarding memory.
    """
    try:
        return bool(
            kernel32.SetProcessWorkingSetSizeEx(
                kernel32.GetCurrentProcess(), ctypes.c_size_t(-1), ctypes.c_size_t(-1), 0
            )
        )
    except (OSError, AttributeError):
        return False
