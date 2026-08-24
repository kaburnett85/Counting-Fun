"""The system tray icon.

pystray sits behind the TrayController protocol, so it can be swapped for a
hand-rolled Shell_NotifyIcon implementation later if its ~20MB ever matters.
It is not worth hand-writing 200 lines of Win32 that cannot be run or debugged
from a Linux development machine.

The icon image is regenerated only when the current job changes -- never on
every tick.
"""

from __future__ import annotations

import contextlib

from ...core.clock import fmt_duration  # noqa: F401  (kept for menu formatting)
from ...logging_setup import get as get_logger

log = get_logger("backends.tray")

ICON_SIZE = 64


class WindowsTray:
    def __init__(self, actions: dict):
        import pystray  # noqa: F401  (fail fast if it is missing)

        self.actions = actions
        self._icon = None
        self._colour = "#3b7dd8"
        self._status = "Starting..."

    # ---- drawing -------------------------------------------------------

    def _image(self, colour: str):
        from PIL import Image, ImageDraw

        image = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse((4, 4, ICON_SIZE - 4, ICON_SIZE - 4), fill=colour)
        # A clock hand, so the icon reads as "tracking" at 16px.
        draw.line((ICON_SIZE / 2, ICON_SIZE / 2, ICON_SIZE / 2, 16), fill="white", width=5)
        draw.line((ICON_SIZE / 2, ICON_SIZE / 2, ICON_SIZE - 20, ICON_SIZE / 2),
                  fill="white", width=5)
        return image

    def _build_menu(self):
        import pystray

        def paused(_item) -> bool:
            getter = self.actions.get("is_paused")
            return bool(getter()) if getter else False

        return pystray.Menu(
            pystray.MenuItem(
                lambda _item: self._status, lambda: None, enabled=False
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Open dashboard", lambda: self._run("open_dashboard"), default=True
            ),
            pystray.MenuItem(
                "Pause tracking", lambda: self._run("toggle_pause"), checked=paused
            ),
            pystray.MenuItem("Pause for 1 hour", lambda: self._run("pause_an_hour")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open data folder", lambda: self._run("open_folder")),
            pystray.MenuItem("Quit", lambda: self._run("quit")),
        )

    def _run(self, name: str) -> None:
        action = self.actions.get(name)
        if action is None:
            return
        try:
            action()
        except Exception:
            log.exception("tray action %s failed", name)

    # ---- protocol ------------------------------------------------------

    def run(self) -> None:
        import pystray

        self._icon = pystray.Icon(
            "TimeSplit", self._image(self._colour), "TimeSplit", self._build_menu()
        )
        self._icon.run()

    def stop(self) -> None:
        if self._icon is not None:
            # Teardown races are harmless here.
            with contextlib.suppress(Exception):
                self._icon.stop()

    def set_status(self, title: str, category_color: str | None = None) -> None:
        self._status = title or "Nothing tracked yet"
        if self._icon is None:
            return
        self._icon.title = f"TimeSplit — {self._status}"
        if category_color and category_color != self._colour:
            self._colour = category_color
            try:
                self._icon.icon = self._image(category_color)
            except Exception:
                log.debug("could not repaint the tray icon")
        with contextlib.suppress(Exception):
            self._icon.update_menu()

    def notify(self, title: str, message: str) -> None:
        if self._icon is None:
            return
        try:
            self._icon.notify(message, title)
        except Exception:
            log.debug("tray notification failed")
