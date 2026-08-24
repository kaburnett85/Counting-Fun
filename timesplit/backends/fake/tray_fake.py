"""A tray that records instead of drawing."""

from __future__ import annotations

import threading


class FakeTray:
    def __init__(self, actions: dict | None = None):
        self.actions = actions or {}
        self.statuses: list[tuple[str, str | None]] = []
        self.notifications: list[tuple[str, str]] = []
        self._stop = threading.Event()

    def run(self) -> None:
        self._stop.wait()

    def stop(self) -> None:
        self._stop.set()

    def set_status(self, title: str, category_color: str | None = None) -> None:
        self.statuses.append((title, category_color))

    def notify(self, title: str, message: str) -> None:
        self.notifications.append((title, message))
