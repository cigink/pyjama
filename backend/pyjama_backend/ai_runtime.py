"""llama.cpp sidecar lifecycle (Phase 10, P10.4-P10.5).

Binds loopback-only, random ephemeral port, random per-launch API key kept in
memory only. Lazy-started on first question, torn down on app exit or when
the AI feature is disabled.
"""

from __future__ import annotations

import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests

from .ai_model import model_path

_HOST = "127.0.0.1"

# Bundled by pyjama.spec: on macOS, backend/vendor/llama-macos/* -> "llama/"
# in the frozen app; the Windows counterpart lives at
# backend/vendor/llama-windows/* once placed there before a Windows build
# (see IMPLEMENTATION_PLAN.md's build notes) — same layout, same lookup.
_EXE_NAME = "llama-server.exe" if sys.platform == "win32" else "llama-server"
_CPU_BACKEND_NAME = "libggml-cpu.dll" if sys.platform == "win32" else "libggml-cpu.so"


class RuntimeError_(Exception):
    pass


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((_HOST, 0))
        return s.getsockname()[1]


def _bundled_llama_dir() -> Path | None:
    """Directory containing the vendored llama-server + its shared
    libraries — `sys._MEIPASS/llama/` when frozen, or the dev-mode
    `backend/vendor/llama-<platform>/` fallback. None if not bundled."""
    meipass = getattr(sys, "_MEIPASS", None)
    vendor_name = "llama-windows" if sys.platform == "win32" else "llama-macos"
    candidates = ([Path(meipass) / "llama"] if meipass else []) + [
        Path(__file__).resolve().parent.parent / "vendor" / vendor_name
    ]
    for d in candidates:
        if (d / _EXE_NAME).exists():
            return d
    return None


def _find_binary() -> tuple[str, Path | None]:
    """Returns (executable_path, bundled_dir). Prefers the app-bundled,
    dependency-free copy (P12's single-installer goal — verified portable
    with zero Homebrew/system llama.cpp install required); falls back to
    PATH for local dev machines that already have llama.cpp installed."""
    bundled = _bundled_llama_dir()
    if bundled:
        return str(bundled / _EXE_NAME), bundled
    exe = shutil.which("llama-server")
    if not exe:
        raise RuntimeError_("llama-server binary not found (not bundled, not on PATH)")
    return exe, None


class InferenceRuntime:
    """One sidecar process per app session, started lazily. Not shared across
    concurrent generate() calls — callers should serialize via the lock."""

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._port: int | None = None
        self._api_key: str | None = None
        self._lock = threading.RLock()

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, timeout_s: float = 20.0) -> None:
        with self._lock:
            if self.is_running():
                return
            mp = model_path()
            if not mp.exists():
                raise RuntimeError_("model is not installed")
            exe, bundled_dir = _find_binary()
            self._port = _free_port()
            self._api_key = secrets.token_hex(24)
            env = os.environ.copy()
            if bundled_dir is not None:
                # Points ggml at our vendored, dependency-free CPU backend
                # instead of its compiled-in default search path (which is
                # an absolute Homebrew/system path that won't exist on a
                # machine without llama.cpp separately installed).
                cpu_backend = bundled_dir / _CPU_BACKEND_NAME
                if cpu_backend.exists():
                    env["GGML_BACKEND_PATH"] = str(cpu_backend)
            self._proc = subprocess.Popen(
                [
                    exe,
                    "--model", str(mp),
                    "--host", _HOST,
                    "--port", str(self._port),
                    "--api-key", self._api_key,
                    "--ctx-size", "4096",
                    "--n-predict", "256",
                ],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                if self._proc.poll() is not None:
                    raise RuntimeError_("llama-server exited during startup")
                try:
                    r = requests.get(f"http://{_HOST}:{self._port}/health", timeout=1)
                    if r.status_code == 200:
                        return
                except requests.RequestException:
                    pass
                time.sleep(0.3)
            self.stop()
            raise RuntimeError_("llama-server did not become healthy in time")

    def health(self) -> bool:
        if not self.is_running():
            return False
        try:
            r = requests.get(f"http://{_HOST}:{self._port}/health", timeout=2)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def generate(self, messages: list[dict], max_tokens: int = 256, timeout_s: float = 30.0) -> str:
        """Deterministic single-shot chat completion (temperature 0, P10.8)."""
        with self._lock:
            if not self.is_running():
                self.start()
            resp = requests.post(
                f"http://{_HOST}:{self._port}/v1/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": "local-sql",
                    "messages": messages,
                    "temperature": 0,
                    "max_tokens": max_tokens,
                    "stream": False,
                },
                timeout=timeout_s,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    def stop(self) -> None:
        with self._lock:
            if self._proc is not None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                self._proc = None
            self._port = None
            self._api_key = None


# Process-wide singleton — one sidecar per running app instance.
runtime = InferenceRuntime()
