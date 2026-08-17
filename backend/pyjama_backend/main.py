"""PyJama desktop launcher.

Starts the FastAPI backend on a background thread, then opens a native window
(pywebview) pointed at it. One Python-launched app; the React UI runs inside the
native webview and calls the localhost API.

Dev:  PYJAMA_DEV_URL=http://localhost:5173 python -m pyjama_backend.main
      (loads the Vite dev server; API served at :8000)
Prod: python -m pyjama_backend.main
      (FastAPI serves the built frontend/dist and the API on one origin)

Headless (no native window, plain localhost web app):
      PYJAMA_NO_WINDOW=1 python -m pyjama_backend.main
      (skips pywebview entirely; open http://127.0.0.1:8000/ in any browser)
"""

from __future__ import annotations

import os
import threading
import time

import uvicorn

from .logging_setup import init, log
from .server import create_app

HOST = "127.0.0.1"
PORT = int(os.environ.get("PYJAMA_PORT", "8000"))


def _serve() -> None:
    uvicorn.run(create_app(), host=HOST, port=PORT, log_level="warning")


def _wait_ready(timeout: float = 20.0) -> None:
    """Block until the backend accepts connections, so pywebview never loads a
    dead URL (which shows a blank window)."""
    import socket

    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((HOST, PORT)) == 0:
                return
        time.sleep(0.2)


def _install_bundled_model() -> None:
    """Single-installer goal: if the app was built with the model bundled
    (pyjama.spec's vendor/models/), copy it into app-data on first launch so
    Local AI works with zero user action. Never blocks/breaks startup if the
    model isn't bundled or the copy fails — the UI already has an "Enable
    Local AI" fallback for that case."""
    try:
        from . import ai_model

        st = ai_model.ensure_installed_from_bundle()
        if st.installed:
            log("local AI model ready", installed=st.installed, verified=st.verified)
    except Exception as e:  # noqa: BLE001
        log("bundled model install skipped", error=str(e))


def main() -> None:
    init()
    log("PyJama backend starting", port=PORT)
    _install_bundled_model()

    server_thread = threading.Thread(target=_serve, daemon=True)
    server_thread.start()
    _wait_ready()

    # In dev, load the Vite server so hot-reload works; otherwise the API origin
    # (which also serves the built UI).
    url = os.environ.get("PYJAMA_DEV_URL") or f"http://{HOST}:{PORT}/"

    if os.environ.get("PYJAMA_NO_WINDOW"):
        # Plain localhost web-app mode — no native window, backend serves the
        # built UI directly at this URL. Server thread is a daemon, so keep
        # the process alive by blocking on it here.
        log("PyJama running headless (no window)", url=url)
        try:
            server_thread.join()
        except KeyboardInterrupt:
            pass
        return

    import webview

    webview.create_window("PyJama — Local Data Workspace", url, width=1280, height=800, min_size=(960, 600))
    webview.start()


if __name__ == "__main__":
    main()
