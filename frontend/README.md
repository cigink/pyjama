# PyJama — Desktop App (Phase 0 skeleton)

Tauri 2 desktop shell: React/TypeScript UI + Rust core. Implements Phase 0 of
`../IMPLEMENTATION_PLAN.md` — the skeleton every later phase builds on.

## Run

```bash
npm install

# UI only, in a browser (mock data, no Rust core)
npm run dev

# Full desktop app (Rust core + webview)
npm run tauri:dev

# Production bundle
npm run tauri:build
```

Rust core lives in `src-tauri/`. Check it directly:

```bash
cargo test  --manifest-path src-tauri/Cargo.toml
cargo clippy --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings
```

## Layout

| Path | Role | Phase 0 story |
|------|------|---------------|
| `src/App.tsx` | UI (ported from the design prototype) | — |
| `src/bridge.ts` | Typed command/event bridge to Rust; browser fallback | P0.2 / P0.3 |
| `src/contracts.ts` | TS mirror of the Rust wire contracts | P0.2 |
| `src-tauri/src/commands.rs` | Typed command surface (§17), mock bodies | P0.2 |
| `src-tauri/src/events.rs` | Event channel names (§17.1) | P0.2 |
| `src-tauri/src/workspace.rs` | Workspace filesystem + manifest (§9.1, §18.1) | P0.4 / P0.5 |
| `src-tauri/src/logging.rs` | JSON logs + `Secret` redaction | P0.6 / P0.7 |
| `src-tauri/src/model.rs` | Domain contracts (§18) | P0.2 |
| `src-tauri/capabilities/default.json` | Narrow Tauri capabilities — no arbitrary fs | P0.4 |
| `../.github/workflows/ci.yml` | Cross-platform CI + signing placeholder | P0.8 / P0.9 |

## Contract sync

`src/contracts.ts` is a hand-maintained mirror of `src-tauri/src/model.rs`. When a
Rust contract changes, update both. `src/bridge.ts` `EVENTS` must match
`src-tauri/src/events.rs`.

## Phase 1 — Databricks read path

Auth + Unity Catalog browsing + warehouse control + a parameterized `SELECT`
spike. Built to be unit-tested without an account (mocked HTTP + in-memory
keystore); point it at a real workspace with env vars:

```bash
export PYJAMA_WORKSPACE_URL="https://<name>.cloud.databricks.com"
export PYJAMA_CLIENT_ID="databricks-cli"   # or your registered public U2M client
export PYJAMA_WAREHOUSE_ID="<sql-warehouse-id>"   # optional
npm run tauri:dev
```

Only non-secret config lives in env. The OAuth sign-in is interactive (browser +
loopback redirect); tokens go to the OS keychain, never to disk or logs.

| Module | Role | Story |
|--------|------|-------|
| `src-tauri/src/oauth.rs` | U2M authorize URL, PKCE code exchange, refresh | P1.1 / P1.3 |
| `src-tauri/src/pkce.rs` | PKCE S256 + CSRF state | P1.1 |
| `src-tauri/src/session.rs` | In-memory session + refresh-skew policy | P1.2 / P1.3 |
| `src-tauri/src/keystore.rs` | OS credential store (+ in-memory fake) | P1.2 / P1.4 |
| `src-tauri/src/auth_service.rs` | Flow orchestration + loopback capture | Epic A |
| `src-tauri/src/dbx_rest.rs` | UC / warehouse / statement REST client | Epics B, C, D |
| `src-tauri/src/dbx_sql.rs` | Identifier quoting + parameterized SELECT | §8.3 / P1.10 |
| `src-tauri/src/config.rs` | Non-secret workspace config from env | Epic A |

Command surface added: `auth_status`, `warehouse_list/get/start`,
`run_select_spike`. `catalog_list`/`schema_list`/`table_list`/`table_get` now hit
real Databricks REST (were mock in Phase 0).

Test the read path without a workspace:

```bash
cargo test --manifest-path src-tauri/Cargo.toml   # 26 tests, all mocked/pure
```

## Not yet real (later phases)

Command bodies return mock data. `manifest.enc` / `operation-journal.enc` are
plaintext JSON until the WDEK encryption hierarchy lands in Phase 2. OAuth,
Databricks REST, DuckDB execution, and the file watcher are all stubs.
