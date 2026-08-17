"""OS-backed secret storage (refresh material, wrapped keys).

Access tokens are NOT stored here — they stay in process memory. Uses the OS
credential store via ``keyring``; an in-memory fake backs tests/CI.
"""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import Protocol

from .logging_setup import log

SERVICE = "com.pyjama.workspace"


class KeyStore(Protocol):
    def set(self, key: str, secret: str) -> None: ...
    def get(self, key: str) -> str | None: ...
    def delete(self, key: str) -> None: ...


class OsKeyStore:
    def set(self, key: str, secret: str) -> None:
        import keyring
        keyring.set_password(SERVICE, key, secret)

    def get(self, key: str) -> str | None:
        import keyring
        return keyring.get_password(SERVICE, key)

    def delete(self, key: str) -> None:
        import keyring
        try:
            keyring.delete_password(SERVICE, key)
        except keyring.errors.PasswordDeleteError:
            pass  # idempotent


class MemoryKeyStore:
    def __init__(self) -> None:
        self._m: dict[str, str] = {}

    def set(self, key: str, secret: str) -> None:
        self._m[key] = secret

    def get(self, key: str) -> str | None:
        return self._m.get(key)

    def delete(self, key: str) -> None:
        self._m.pop(key, None)


def _cache_path() -> Path:
    system = platform.system()
    if system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    elif system == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "PyJama" / "token-cache.json"


class FileKeyStore:
    """0600 file token cache, mirroring the Databricks CLI's own token-cache
    approach. Used as a fallback when the OS keychain is unavailable (e.g. an
    unsigned app bundle). Weaker than the keychain — protected by file
    permissions, not the OS secure enclave. Sign the app to use the keychain."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _cache_path()

    def _read(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def _write(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Create with 0600 before writing secret content.
        fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        if platform.system() != "Windows":
            os.chmod(self._path, 0o600)

    def set(self, key: str, secret: str) -> None:
        d = self._read()
        d[key] = secret
        self._write(d)

    def get(self, key: str) -> str | None:
        return self._read().get(key)

    def delete(self, key: str) -> None:
        d = self._read()
        if d.pop(key, None) is not None:
            self._write(d)


class FallbackKeyStore:
    """Try the OS keychain; on any keychain error fall back to a 0600 file cache.

    Once a fallback happens, stay on the file store for consistency within the
    session (so a token written to the file is later found there)."""

    def __init__(self) -> None:
        self._os = OsKeyStore()
        self._file = FileKeyStore()
        self._use_file = False

    def set(self, key: str, secret: str) -> None:
        if not self._use_file:
            try:
                self._os.set(key, secret)
                return
            except Exception as e:  # noqa: BLE001
                self._use_file = True
                log("keychain unavailable; using 0600 file token cache (sign the app to use Keychain)", reason=type(e).__name__)
        self._file.set(key, secret)

    def get(self, key: str) -> str | None:
        if self._use_file:
            return self._file.get(key)
        try:
            return self._os.get(key)
        except Exception:  # noqa: BLE001
            self._use_file = True
            return self._file.get(key)

    def delete(self, key: str) -> None:
        # Delete from both to be safe.
        try:
            self._os.delete(key)
        except Exception:  # noqa: BLE001
            pass
        self._file.delete(key)
