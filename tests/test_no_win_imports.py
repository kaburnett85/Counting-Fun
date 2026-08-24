"""The core must never import anything Windows-only.

This is what keeps the app testable on Linux. If a ctypes.wintypes or pystray
import creeps into the classifier or the tracker, the entire test suite becomes
unrunnable here -- so it is checked directly, by reading the source.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "timesplit"

#: Packages that must stay platform-neutral.
PURE_PACKAGES = ["core", "store", "web", "export", "llm", "wizard"]

FORBIDDEN_ROOTS = {
    "winreg", "msvcrt", "pystray", "uiautomation", "comtypes", "win32api",
    "win32gui", "win32process", "pywintypes", "PIL",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def _pure_files() -> list[Path]:
    files: list[Path] = []
    for package in PURE_PACKAGES:
        files.extend(sorted((ROOT / package).rglob("*.py")))
    return files


@pytest.mark.parametrize("path", _pure_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_core_modules_are_platform_neutral(path):
    for module in _imports(path):
        root = module.split(".")[0]
        assert root not in FORBIDDEN_ROOTS, f"{path.name} imports {module}"
        assert not module.startswith("ctypes.wintypes"), f"{path.name} imports {module}"


def test_windows_adapters_are_thin():
    """Decisions belong in the core, which is tested; these cannot be.

    A line budget is a blunt instrument, but it is an effective one: it stops
    logic drifting into the one place we cannot run.
    """
    budget = {
        "capture_win.py": 200,
        "idle_win.py": 150,
        "uia_win.py": 260,
        "tray_win.py": 180,
        "autostart_win.py": 220,
        "dpapi_win.py": 140,
        "proc_win.py": 120,
    }
    for name, limit in budget.items():
        path = ROOT / "backends" / "win" / name
        if not path.exists():
            continue
        lines = len(path.read_text(encoding="utf-8").splitlines())
        assert lines <= limit, f"{name} is {lines} lines (budget {limit})"


def test_the_app_imports_and_picks_a_backend_on_this_platform():
    from timesplit.backends.factory import get_autostart, get_capture, get_secrets, get_tray
    from timesplit.config import Config

    cfg = Config()
    assert get_capture(cfg) is not None
    assert get_tray({}) is not None
    assert get_autostart().status() is not None
    assert get_secrets().describe()

    import timesplit.app  # noqa: F401
    import timesplit.doctor  # noqa: F401


@pytest.mark.skipif(sys.platform == "win32", reason="Windows genuinely has these")
def test_windows_modules_are_importable_only_as_source():
    """On Linux the win/ modules must not be imported at startup.

    Each one raises on import here, which is precisely why nothing in the core
    may depend on them.
    """
    import timesplit.backends.factory as factory

    assert not factory.is_windows()
