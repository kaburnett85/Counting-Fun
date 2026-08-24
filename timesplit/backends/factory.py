"""Chooses the Windows adapters or the fakes, by platform.

Importing this module must never pull in a Windows-only dependency on Linux --
the win/ imports happen inside the functions for exactly that reason.
"""

from __future__ import annotations

import sys
from typing import Any

from ..config import Config
from ..logging_setup import get as get_logger

log = get_logger("backends")


def is_windows() -> bool:
    return sys.platform == "win32"


def get_capture(cfg: Config) -> Any:
    if is_windows():
        try:
            from .win.capture_win import WindowsCaptureBackend

            return WindowsCaptureBackend(cfg)
        except Exception as exc:  # pragma: no cover - Windows only
            log.error("Windows capture unavailable (%s); falling back to a null backend", exc)
    from .fake.capture_fake import ListCaptureBackend

    return ListCaptureBackend([])


def get_tray(actions: dict) -> Any:
    if is_windows():
        try:
            from .win.tray_win import WindowsTray

            return WindowsTray(actions)
        except Exception as exc:  # pragma: no cover - Windows only
            log.error("tray unavailable (%s); running without a tray icon", exc)
    from .fake.tray_fake import FakeTray

    return FakeTray(actions)


def get_autostart() -> Any:
    if is_windows():
        try:
            from .win.autostart_win import TaskSchedulerAutostart

            return TaskSchedulerAutostart()
        except Exception as exc:  # pragma: no cover - Windows only
            log.error("autostart unavailable: %s", exc)
    from .fake.autostart_fake import FakeAutostart

    return FakeAutostart()


def get_secrets() -> Any:
    if is_windows():
        try:
            from .win.dpapi_win import DpapiSecretStore

            return DpapiSecretStore()
        except Exception as exc:  # pragma: no cover - Windows only
            log.error("DPAPI unavailable (%s); using the environment variable instead", exc)
    from .fake.secrets_fake import EnvSecretStore

    return EnvSecretStore()
