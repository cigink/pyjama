# PyJama — Governed Local Data Workspace

FastAPI backend + React/TypeScript frontend, with a Databricks Unity Catalog
integration, a local (offline) NL-to-SQL AI assistant, and a DuckDB-native
analytical engine ("Explore" mode). Runs either as a native desktop app
(pywebview) or headless as a plain localhost web app.

## Prerequisites

- Python 3.11+
- Node 20+
- macOS or Windows (Linux backend works; native window mode untested there)

## 1. Backend setup

```bash
cd backend
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

## 2. Frontend setup

```bash
cd frontend
npm install
```

## 3. Run in dev mode

Two terminals — Vite dev server (hot reload) + backend:

```bash
# Terminal 1 — frontend
cd frontend
npm run dev            # serves UI at http://localhost:5173

# Terminal 2 — backend, pointed at the Vite server
cd backend
PYJAMA_DEV_URL=http://localhost:5173 ./.venv/bin/python -m pyjama_backend.main
```

This opens a native window. Backend API always listens on `http://127.0.0.1:8000`.

## 4. Run as a plain localhost web app (no native window)

Build the frontend once, then run the backend headless:

```bash
cd frontend && npm run build      # outputs frontend/dist

cd ../backend
PYJAMA_NO_WINDOW=1 ./.venv/bin/python -m pyjama_backend.main
```

Open `http://127.0.0.1:8000/` in any browser. No pywebview, no packaging —
the backend serves the built UI and API from one origin.

## 5. Run tests

```bash
cd backend
./.venv/bin/python -m pytest pyjama_backend/tests/ -q
```

## 6. Local AI assistant (optional)

The NL-to-SQL assistant runs entirely offline via a bundled `llama.cpp`
sidecar (`backend/vendor/llama-macos/`) and a local GGUF model.

- The model itself (~644MB) is **not** checked into git — see
  `backend/pyjama_backend/ai_model.py` for the pinned manifest
  (`cycloneboy/SLM-SQL-0.5B`, CC-BY-NC-4.0).
- To enable it locally: place the matching GGUF at
  `backend/vendor/models/SLM-SQL-0.5B.Q8_0.gguf` (checksum must match the
  manifest's `sha256`), then start the app — it auto-installs into app-data
  on first launch via `ensure_installed_from_bundle()`.
- Without the model present, the rest of the app works normally; the AI
  assistant just reports itself as not installed.

## 7. Building a standalone desktop app (macOS)

```bash
cd frontend && npm run build
cd ../backend
./.venv/bin/pip install pyinstaller
./.venv/bin/pyinstaller pyjama.spec --noconfirm --distpath dist_app --workpath build_app
open dist_app/PyJama.app
```

To bundle the model + AI sidecar into the package, populate
`backend/vendor/models/` and `backend/vendor/llama-macos/` before running
PyInstaller (see `pyjama.spec`). Windows builds need the equivalent
`backend/vendor/llama-windows/` binaries, built on a Windows machine — not
producible from macOS.

## Project layout

```
backend/pyjama_backend/   FastAPI app, Databricks auth, DuckDB query engine,
                           Explore analytical engine, local AI assistant
backend/pyjama_backend/tests/  pytest suite
backend/vendor/            Vendored llama.cpp binaries + (gitignored) model
frontend/src/               React/TS UI
IMPLEMENTATION_PLAN.md      Full phase-by-phase build history and design notes
```

## Environment variables

| Variable            | Purpose                                                        |
|----------------------|------------------------------------------------------------------|
| `PYJAMA_PORT`        | Backend port (default `8000`)                                   |
| `PYJAMA_DEV_URL`      | Load this URL in the native window instead of the built UI (dev)|
| `PYJAMA_NO_WINDOW`    | Skip the native window; run as headless localhost web app       |
