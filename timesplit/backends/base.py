"""Platform contracts.

Everything the app needs from the operating system is expressed here as a
Protocol. The Windows implementations in ``win/`` are thin adapters that
satisfy these and contain no decision logic; the fakes in ``fake/`` satisfy
the same contracts so the whole application can run and be tested on Linux.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from ..core.models import Snapshot


@runtime_checkable
class CaptureBackend(Protocol):
    """Produces one Snapshot per poll: foreground window, plus idle state."""

    def start(self) -> None: ...

    def capture(self) -> Snapshot: ...

    def stop(self) -> None: ...

    @property
    def supports_urls(self) -> bool: ...

    def describe(self) -> dict[str, str]:
        """Human-readable facts for `doctor`."""
        ...


@runtime_checkable
class TrayController(Protocol):
    def run(self) -> None:
        """Block on the platform's UI loop until stop() is called."""
        ...

    def stop(self) -> None: ...

    def set_status(self, title: str, category_color: str | None = None) -> None: ...

    def notify(self, title: str, message: str) -> None: ...


@runtime_checkable
class AutostartManager(Protocol):
    def install(self) -> tuple[bool, str]: ...

    def uninstall(self) -> tuple[bool, str]: ...

    def status(self) -> tuple[bool, str]: ...


@runtime_checkable
class SecretStore(Protocol):
    """Stores the optional Claude API key outside config and the database."""

    def set_secret(self, value: str) -> None: ...

    def get_secret(self) -> str | None: ...

    def clear_secret(self) -> None: ...

    def describe(self) -> str: ...


#: Menu wiring passed from the app into whichever tray implementation is active.
TrayActions = dict[str, Callable[[], None]]
