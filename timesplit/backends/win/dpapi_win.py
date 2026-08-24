"""Storing the optional Claude API key with Windows DPAPI.

Encrypted to your Windows account, so another account on the same machine
cannot read it. Never written to config.json, never into the database, never
into the log.
"""

from __future__ import annotations

import contextlib
import ctypes
from ctypes import wintypes

from ... import paths
from ...logging_setup import get as get_logger

log = get_logger("backends.dpapi")

crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

DESCRIPTION = "TimeSplit API key"


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> DATA_BLOB:
    buffer = ctypes.create_string_buffer(data, len(data))
    return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))


def _extract(blob: DATA_BLOB) -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        kernel32.LocalFree(blob.pbData)


class DpapiSecretStore:
    def __init__(self) -> None:
        self.path = paths.secret_path()

    def set_secret(self, value: str) -> None:
        if not value:
            self.clear_secret()
            return
        blob_in = _blob(value.encode("utf-8"))
        blob_out = DATA_BLOB()
        # CRYPTPROTECT_LOCAL_MACHINE is deliberately not set, so the key is
        # bound to this user account rather than the whole machine.
        if not crypt32.CryptProtectData(
            ctypes.byref(blob_in), DESCRIPTION, None, None, None, 0, ctypes.byref(blob_out)
        ):
            raise OSError("could not encrypt the API key")
        data = _extract(blob_out)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(data)
        with contextlib.suppress(OSError):
            self.path.chmod(0o600)

    def get_secret(self) -> str | None:
        import os

        env = os.environ.get("ANTHROPIC_API_KEY")
        if env:
            return env
        try:
            data = self.path.read_bytes()
        except OSError:
            return None
        blob_in = _blob(data)
        blob_out = DATA_BLOB()
        if not crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        ):
            log.warning("stored API key could not be decrypted (different Windows account?)")
            return None
        return _extract(blob_out).decode("utf-8", "replace")

    def clear_secret(self) -> None:
        with contextlib.suppress(OSError):
            self.path.unlink()

    def describe(self) -> str:
        import os

        if os.environ.get("ANTHROPIC_API_KEY"):
            return "read from the ANTHROPIC_API_KEY environment variable"
        key = self.get_secret()
        if key:
            return f"stored, encrypted to your Windows account (ends …{key[-4:]})"
        return "not set"
