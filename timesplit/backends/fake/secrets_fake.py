"""A secret store for non-Windows use.

Windows gets DPAPI. Everywhere else there is no OS-backed keystore we can rely
on, so this reads ANTHROPIC_API_KEY from the environment and refuses to write
a key to disk in plaintext -- silently storing a credential unencrypted would
be worse than not storing it.
"""

from __future__ import annotations

import os


class EnvSecretStore:
    ENV_VAR = "ANTHROPIC_API_KEY"

    def __init__(self) -> None:
        self._runtime: str | None = None

    def set_secret(self, value: str) -> None:
        # Held for this process only; never written to disk.
        self._runtime = value or None

    def get_secret(self) -> str | None:
        return self._runtime or os.environ.get(self.ENV_VAR) or None

    def clear_secret(self) -> None:
        self._runtime = None

    def describe(self) -> str:
        if self._runtime:
            return "set for this session only (not saved to disk)"
        if os.environ.get(self.ENV_VAR):
            return f"read from ${self.ENV_VAR}"
        return "not set"
