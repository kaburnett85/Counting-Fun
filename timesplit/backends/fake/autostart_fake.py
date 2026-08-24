from __future__ import annotations


class FakeAutostart:
    def __init__(self) -> None:
        self.installed = False

    def install(self) -> tuple[bool, str]:
        self.installed = True
        return True, "autostart is not available on this platform (no-op)"

    def uninstall(self) -> tuple[bool, str]:
        self.installed = False
        return True, "autostart is not available on this platform (no-op)"

    def status(self) -> tuple[bool, str]:
        return self.installed, "starting at login is only available on Windows"
