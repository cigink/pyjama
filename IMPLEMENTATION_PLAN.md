# Governed Local Data Workspace — Phase-by-Phase Implementation Plan

**Source docs:** `Governed Local Data Workspace MVP.docx` (PRD), `Technical_Design_Governed_Local_Data_Workspace_MVP.docx` (Tech Design v0.1).

**Stack:** Tauri 2 (Rust core + React/TypeScript UI), DuckDB + Apache Arrow for local execution, encrypted Parquet vault at rest, Databricks REST API from Rust.

**Guiding principle:** The source system manages enterprise-scale data; the workspace manages the data required for the user's task. The desktop app is a compute and interaction tier — *not* the authoritative data store. Unity Catalog and the target Delta table remain the system of record.

**Golden scenario drives every phase:**

> sign in → browse Unity Catalog → filter checkout → local data grid → join local XLSX → deduplicate → standardize values → manual edit → validate → review diff → commit MERGE → new Delta version.

Section references below (§) point to the Technical Design doc unless noted `PRD §`.

---

## Phase 0 — Skeleton & Foundations

**Goal:** an empty app that builds, ships, and passes messages between Rust and the UI.

- Tauri 2 project: React/TS frontend, Rust core crate.
- Typed command/event bridge — all commands from §17 stubbed, returning mock data.
- Workspace filesystem abstraction: `<AppData>/workspaces/<uuid>/` layout (§9.1).
- CI: macOS + Windows builds; code-signing pipeline placeholder.
- Structured JSON logging with operation IDs; **redaction rules baked in from day one** — never log tokens, presigned URLs, SQL parameter values (§22.1).

**Exit:** app opens; one round-trip command works; CI green on both platforms.

### Phase 0 — Task / Story Breakdown

Foundational. Epic E (bridge) and Epic F (workspace fs) are the load-bearing pieces every later phase builds on; get their contracts right early. Epic G (logging/redaction) and Epic H (CI/signing) are cross-cutting and cheap to add now, painful to retrofit.

#### Epic E — App Shell & Rust↔UI Bridge (§4, §17)

**P0.1 — Tauri 2 project scaffold**
As a developer, I have a running Tauri app with a React/TS frontend and a Rust core crate.
- Tauri 2 workspace; React + TypeScript UI; separate Rust core crate for domain logic.
- **AC:** `dev` launches a window rendering the React app; `build` produces a native bundle.

**P0.2 — Typed command/event contract**
- Define command + event surface from §17 (auth, catalog, checkout, pipeline, preview, diff, validation, commit, watcher) as typed Rust handlers + TS bindings; all return mock data for now.
- Event channels stubbed: `checkout://*`, `pipeline://*`, `watcher://*`, `commit://*`, `auth://expired` (§17.1).
- **AC:** UI invokes each command and gets a typed mock response; subscribing to a stub event delivers a payload; type mismatch fails at compile.

**P0.3 — Round-trip smoke path**
- One real end-to-end command (e.g. `ping` → Rust → response) wired UI-to-core-to-UI.
- **AC:** button click triggers Rust handler, response renders; failure surfaces as a typed error, not a silent hang.

#### Epic F — Workspace Filesystem Abstraction (§9.1)

**P0.4 — Workspace directory layout**
As a developer, I have a managed `<AppData>/workspaces/<uuid>/` structure.
- Create/open/enumerate workspace dirs; layout skeleton: `manifest.enc`, `operation-journal.enc`, `data/`, `local_sources/`, `changes/`, `cache/` (§9.1).
- Restrict workspace directory permissions; never place data in Downloads/Documents (PRD §21).
- **AC:** creating a workspace yields the dir tree with correct permissions; enumerate lists existing workspaces; paths resolve cross-platform (macOS + Windows).

**P0.5 — Manifest read/write scaffold**
- Serde model for the workspace manifest (§18.1) — write/read round-trip (plaintext placeholder now; encryption lands Phase 2).
- **AC:** write a manifest, reopen app, read it back identical.

#### Epic G — Observability Foundation (§22.1)

**P0.6 — Structured logging with operation IDs**
- JSON logs carrying operation IDs, durations, error codes.
- **AC:** an operation emits correlated start/end log lines sharing one operation ID.

**P0.7 — Redaction rules (day-one)**
As a security-conscious team, secrets never reach logs.
- Central redaction: never log access/refresh tokens, presigned URLs, SQL parameter values (§22.1, §19.4).
- **AC:** logging a struct holding a token/URL emits a redacted placeholder; test asserts no sentinel secret appears in log output.

#### Epic H — Build, CI & Packaging

**P0.8 — CI cross-platform builds**
- CI builds + tests on macOS and Windows on every push.
- **AC:** green pipeline on both platforms; failing build/test blocks merge.

**P0.9 — Code-signing pipeline placeholder**
- Signing steps wired (macOS notarization / Windows Authenticode) behind config; no-op without certs.
- **AC:** signing stage runs (skips cleanly when certs absent); documented path to enable for release builds.

**Phase 0 done when:** P0.1–P0.9 pass — app opens, one round-trip command works, workspace dir scaffolds correctly, redacted logging in place, CI green on both platforms.

---

## Phase 1 — Databricks Read Path

**Goal:** authenticate, browse Unity Catalog, reach a warehouse.

- OAuth U2M flow in Rust (§6). Access token memory-only; refresh material in OS key store (macOS Keychain / Windows DPAPI).
- `AuthSession` state; refresh-on-expiry; sign-in / sign-out / reauth prompts.
- UC browser via REST (§7): list catalogs / schemas / tables, get-table. Cache metadata briefly; treat Databricks as authoritative.
- Warehouse select + start-if-stopped (§8.2); poll `STARTING` → `RUNNING`.
- Statement Execution spike (small JSON result) proving auth + warehouse + query end-to-end.

**Risk:** OAuth U2M in raw Rust (no Python SDK). De-risk this first.

**Exit:** sign in, browse the catalog tree, run a trivial `SELECT`, see rows.

### Phase 1 — Task / Story Breakdown

Ordered. Auth stories (P1.1–P1.4) gate everything; do them first. Browser (P1.5–P1.7) and warehouse (P1.8–P1.9) can run in parallel once a token exists. P1.10 is the integration spike proving the whole read path.

#### Epic A — Authentication (§6, §19.3)

**P1.1 — OAuth U2M authorization flow**
As a user, I sign in with my company Databricks identity so I only access data I'm authorized for.
- Rust implements documented Databricks U2M authorization-code + PKCE flow; loopback redirect capture.
- User enters workspace URL, browser opens for consent, app receives code, exchanges for tokens.
- **AC:** valid consent yields an access token + refresh material; wrong URL / denied consent shows a clear error, no partial state.

**P1.2 — Secure token storage & session state**
- `AuthSession` struct (§6.1): access token memory-only (`SecretString`), refresh material in OS store (Keychain / DPAPI) via keyed reference.
- **AC:** access token never touches disk; grep of app data + logs finds no token/refresh material; refresh ref present in OS store.

**P1.3 — Token refresh & expiry handling**
- Detect near-expiry; refresh before remote calls. On refresh failure, pause remote ops (local work unaffected — §6.2), emit `auth://expired`.
- **AC:** expired access token auto-refreshes transparently; forced refresh failure surfaces reauth prompt, does not crash.

**P1.4 — Sign-out**
- `auth_logout` removes refresh credentials from OS store, zeroes in-memory token, closes remote session.
- **AC:** after logout, remote calls require fresh sign-in; no refresh material remains in OS store.

#### Epic B — Unity Catalog Browser (§7)

**P1.5 — List catalogs / schemas / tables**
As a user, I browse Unity Catalog to find my table.
- REST: `GET /catalogs`, `/schemas?catalog_name`, `/tables?catalog_name&schema_name` (§7 table). Lazy-load tree nodes.
- **AC:** tree renders catalogs → schemas → tables; only resources the user can access appear (server-enforced); expand loads children on demand.

**P1.6 — Table detail / schema inspection**
- `GET /tables/{full_name}`: columns, types, properties, row-count metadata where available (`table_get` §17).
- **AC:** selecting a table shows column list + types; row-filter/column-mask metadata displayed when reported (enforcement stays server-side — §7.1).

**P1.7 — Metadata cache**
- Short-lived cache of non-row metadata; Databricks authoritative; manual refresh invalidates.
- **AC:** repeat browse of same node serves from cache within TTL; refresh forces re-fetch.

#### Epic C — Warehouse (§8.2)

**P1.8 — Warehouse selection**
- List available SQL warehouses (Warehouses API); user/admin selects target; persist choice.
- **AC:** user picks from warehouses they can access; selection persists across restart.

**P1.9 — Start stopped warehouse**
- If `STOPPED` and user authorized, `POST /warehouses/{id}/start`; poll state `STARTING` → `RUNNING`. Never auto-stop a shared warehouse.
- **AC:** starting a stopped warehouse reaches `RUNNING` with progress shown; unauthorized start surfaces the Databricks error; running warehouse used directly.

#### Epic D — Integration Spike

**P1.10 — Statement Execution read-path spike**
As a developer, I prove auth + warehouse + query end-to-end before building encrypted checkout.
- Submit small `SELECT` (JSON disposition), poll `GET /statements/{id}`, render rows in a throwaway view.
- Parameterized values + identifier encoder validated here (feeds §8.3 / Phase 2).
- **AC:** signed-in user runs a bounded `SELECT` against the selected warehouse and sees returned rows; auth failure / warehouse-not-running / bad query each produce a distinct handled error.

**Phase 1 done when:** P1.1–P1.10 pass — sign in, browse tree, inspect a table, start a warehouse, run a `SELECT`, see rows; no credentials on disk or in logs.

---

## Phase 2 — Encrypted Checkout  *(highest-risk phase)*

**Goal:** governed remote reduction streamed into an encrypted local vault.

- SQL generation with parameterized values + a dedicated identifier encoder (§8.3). Never accept identifiers from free-form text.
- Statement Execution: `ARROW_STREAM` + `EXTERNAL_LINKS`; poll with exponential backoff (§8.4–8.5).
- Chunk download: fetch presigned link, **strip the Databricks Authorization header** on the storage request (§8.6); stream bytes.
- Arrow IPC decoder → bounded `RecordBatch`es; no full-result concatenation in memory (§9.4).
- Key hierarchy (§9.2): random per-workspace WDEK, wrapped by an App Master Key held in the OS credential store.
- Encrypted Parquet write via DuckDB `add_parquet_key` + `ENCRYPTION_CONFIG` (§9.3).
- Resumable chunk journal (§8.7, §18.3): persist completed chunk indexes, fsync, resume on crash or URL expiry.

**Note:** default local-checkout policy capped well below API limit (e.g. 10 GiB — §20.1) even though `EXTERNAL_LINKS` permits up to 100 GiB.

**Exit:** filtered checkout lands encrypted Parquet partitions; kill app mid-download, restart, resume completes. No plaintext dataset file on disk.

---

## Phase 3 — Workspace & Data Grid

**Goal:** see the data.

- `workspace_open`: load manifest, register encrypted partitions as a DuckDB relation.
- Pipeline metadata model — declarative JSON DAG, linear in MVP (§10, §28). Source step only for now.
- Preview service (§11): windowed `preview_query`, 500-row default, dedicated cancellable DuckDB connection.
- Virtualized data grid UI: scroll, column resize, sort, null display, data-type indicators, cell/row select, copy (PRD §10).
- Status bar: rows • size • execution location • save state (PRD §22).
- Workspace persistence across app restart (PRD §20).

**Exit:** open workspace, scroll 1M+ rows smoothly, sort/filter in grid, reopen after restart.

---

## Phase 4 — Core Transforms

**Goal:** the deterministic transformation pipeline.

- Step compiler: DAG → chained CTE DuckDB SQL (§10.2).
- Steps: filter; select / remove / reorder columns; rename; formula (arithmetic, string concat, conditional, date ops, null handling); deduplicate (`QUALIFY ROW_NUMBER()`); replace / standardize values (PRD §8.1–8.7).
- Invalidation (§10.3): config hash per step; changing step N invalidates N-onward; lazy recompute on select.
- Add-step UX (PRD §9); per-step error binding (PRD §24).

**Exit:** build filter → formula → dedupe → replace chain; edit an upstream step; downstream steps invalidate and recompute.

### Phase 4 — Task / Story Breakdown

Moves transforms from JavaScript-on-a-window (`logic.ts`, mock-only) to
**server-side DuckDB**, compiled from the declarative step list and executed over
the decrypted workspace relation. The Phase 3 windowed grid then previews *any*
step's output. Ordering: Epic Q (compiler) underpins all steps; R adds each step;
S wires execution/preview/persistence; T is the UI.

#### Epic Q — Step model & SQL compiler

**P4.1 — Declarative step schema**
- Typed `StepSpec` (`id`, `ordinal`, `type`, `enabled`, `config` JSON) + validation per type. Pipeline is a linear list (schema allows multi-input later for joins).
- **AC:** invalid config (unknown column, bad operator) is rejected with a typed error before compilation.

**P4.2 — DuckDB CTE compiler**
- Compile the pipeline to chained CTEs (`s0` = source `read`, `s1` = filter over `s0`, …); return the SQL that produces step N's output (§10.2).
- **AC:** a 3-step pipeline compiles to one `WITH … SELECT * FROM sN` query; selecting an earlier step compiles only up to it.

**P4.3 — Injection-safe compilation + config hash**
- DuckDB-dialect identifier quoting (double-quote) for all columns/tables; user values as bound parameters, never interpolated. Stable `config_hash` per step.
- **AC:** a value containing `'; DROP …` stays a bound parameter; identifiers from metadata only; changing a config changes its hash.

#### Epic R — Transform steps (each compiles + previews)

**P4.4 — Filter** — multi-condition `WHERE` (all MVP operators, reuse `dbsql`).
**P4.5 — Select / remove / reorder columns** — projection with explicit order.
**P4.6 — Rename column** — `SELECT old AS new`.
**P4.7 — Formula / derived column** — arithmetic, string concat, conditional (`CASE`), date ops, null handling.
**P4.8 — Deduplicate** — `QUALIFY ROW_NUMBER() OVER (PARTITION BY key ORDER BY …)` = 1; keep latest/first/last.
**P4.9 — Replace / standardize values** — `CASE`/mapping over a column.
- **AC (each):** step config compiles to correct SQL; preview shows the transformed rows; input row-count → output row-count reported.

#### Epic S — Execution, preview & persistence

**P4.10 — Windowed preview by step**
- `preview_query` gains `step_id`: compile up to that step, wrap with sort + `LIMIT/OFFSET`.
- **AC:** selecting any step shows its windowed output in the grid.

**P4.11 — Per-step row count & output schema**
- Endpoint returns row count + output columns/types for a step (drives the pipeline panel + downstream column pickers).
- **AC:** each step row in the UI shows an accurate post-step row count.

**P4.12 — Invalidation & lazy recompute**
- Config-hash-based invalidation: editing step N invalidates cached results ≥ N; recompute lazily on select. Optional in-memory result cache keyed by (pipeline_revision, step_id, window).
- **AC:** editing an upstream step updates all downstream previews; unchanged steps aren't recomputed unnecessarily.

**P4.13 — Persist pipeline in manifest**
- Steps saved to the manifest; reopening a workspace restores the full pipeline (§20).
- **AC:** build a pipeline, restart the app, reopen — the steps are all there.

#### Epic T — UI wiring

**P4.14 — Real add-step + config modals** — filter/select/rename/formula/deduplicate/replace modals wired to the backend against live columns (not mock).
**P4.15 — Live pipeline panel** — real per-step summaries + server row counts; selecting a step previews its output in the windowed grid.
**P4.16 — Edit-upstream recompute + errors** — changing/removing a step recomputes downstream; per-step error binding (PRD §24).
- **AC:** on live data, build filter → formula → dedupe → replace, click through each step's preview, edit an upstream step and watch downstream update; a bad step shows its error inline.

**Phase 4 done when:** P4.1–P4.16 pass — a live checked-out workspace supports the full visual transform chain server-side, previews per step in the windowed grid, recomputes on upstream edits, and restores the pipeline after restart.

---

## Phase 5 — Local Files  *(critical MVP capability)*

**Goal:** join enterprise data with a local file.

- Watched folder (§12, PRD §14): Rust `notify` watcher; stability check (two stable size+mtime observations); fingerprint; type detect.
- Readers: DuckDB `read_csv` (auto-detect + delimiter/header override), `read_parquet`, `read_xlsx` excel extension (`.xlsx` only).
- Managed import: copy local file → encrypted managed Parquet. Original stays plaintext and user-owned; managed workspace copy is encrypted.
- Schema preview + "Use in workspace".
- `join_file` step: left / inner; match keys; matched/unmatched % preview; type-mismatch error surfacing (PRD §24).

**Exit:** drop XLSX in watched folder → detected → join on `customer_id` → grid shows joined columns.

---

## Phase 6 — Diff, Row Identity & Validation

**Goal:** know exactly what changed and prove it is safe.

- Row identity (§13.1, PRD §16): user picks key column(s); verify local uniqueness; block commit if duplicates exist.
- Manual edit overlay (PRD §8.8, App. B.3): edits stored as a keyed overlay relation — never an in-place source mutation.
- Diff engine (§13.2): BASE vs FINAL anti-joins for added/deleted; canonical row-hash compare for updated; field-level diff.
- Validation engine (§14): rules compile to DuckDB boolean expressions producing a rule-result relation; `error` blocks commit, `warning` is reviewable but non-blocking.
- Diff UI (All / Added / Modified / Deleted filters); validation view (show failed rows, which rule failed, correct, rerun).

**Exit:** manually edit ~20 rows, run validation, review changed rows with before/after per field.

---

## Phase 7 — Commit Write-Back

**Goal:** governed write back to Unity Catalog.

- Build staged change set: Parquet carrying row key + `_op` (INSERT / UPDATE / DELETE) + full target values (§15).
- Upload to a UC Volume via the Files API; unique path per `commit_id`; partition files under the 5 GiB limit (§15.2).
- Version conflict check (§16): record Delta version via `DESCRIBE HISTORY` at checkout; recheck before commit; block if changed.
- MERGE in Databricks SQL with **explicit column enumeration** (no `SET *` — §15.3).
- Commit journal + metadata (§15.4); staging cleanup; janitor for abandoned staging paths past TTL.
- Commit-readiness gate (PRD §17): pipeline succeeded + unique row identity + no blocking validation errors + source writable + user authenticated.
- **Refresh & Reapply** on conflict (§16.2): preserve pipeline + manual-edit overlay, fresh checkout, re-run transforms, reapply edits by row key, flag orphaned edits, re-validate and re-diff.

**Exit:** MERGE succeeds and a new Delta version is recorded; a concurrent source change blocks the commit and offers Refresh & Reapply.

---

## Phase 8 — Hardening & MVP-Complete

**Goal:** meet the Definition of MVP Complete (§24.1).

- Crash recovery across checkout / commit / import (§21 resumability table).
- Security tests (§23.3): plaintext-sentinel scan of workspace dir; encrypted Parquet unreadable without key; no auth header on external-link requests; token/URL redaction in logs and crash reports; Tauri capability lockdown (no arbitrary frontend filesystem/network access).
- Performance controls (§20): checkout size policy (< 2 GiB default, 2–10 GiB warn, > 10 GiB block); DuckDB `memory_limit` at 50–60% RAM; disk pre-checks before checkout.
- Full golden end-to-end test (§23.4), including mid-download restart and blocked-then-reapplied commit.
- Packaging + code signing on both platforms.

### Pending — code signing & secret storage (deferred from earlier phases)

The desktop app currently ships **unsigned** (ad-hoc PyInstaller `.app`). This
forces several dev-grade tradeoffs that must be resolved here before release:

- **macOS code signing + notarization** — sign the `.app` with a Developer ID and
  notarize. Fixes: (a) stable app identity so macOS Keychain stops prompting on
  every launch / rebuild, (b) Gatekeeper trust so users can open it without the
  "unidentified developer" warning.
- **Windows Authenticode signing** — sign the `.exe`/installer.
- **Move secrets back to the OS keychain.** Because an unsigned app is denied /
  repeatedly prompted by Keychain, the frozen build falls back to a `0600` file
  token cache (`~/Library/Application Support/PyJama/token-cache.json`) holding the
  OAuth **refresh token** and per-workspace **WDEKs** (`PYJAMA_KEYSTORE=file`).
  Once signed, default the frozen build back to `FallbackKeyStore`/`OsKeyStore`
  (Keychain / DPAPI) and migrate any file-cached secrets in. Add the
  `keychain-access-groups` entitlement (macOS).
- **App master key (AMK) wrapping of the WDEK** (deferred from Phase 2, §9.2):
  wrap each workspace WDEK with an app master key held in the secure store, rather
  than storing the WDEK directly. Currently the WDEK is stored directly in the
  keystore.
- **Bundle icon** — the app ships with no icon; add one to the PyInstaller spec.
- **Encrypt the operation journal + manifest** — currently plaintext JSON
  (`.enc` names are a contract only). Encrypt alongside the workspace on the same
  key hierarchy.
- **Harden the local HTTP surface** — the FastAPI backend binds `127.0.0.1:8000`;
  before release, bind an ephemeral port, add a per-session token the UI must
  present, and lock CORS down to the packaged origin only.
- **CI signing job** — wire real Developer ID / Authenticode certs into the
  release build (the `sign` job in `.github/workflows/ci.yml` is a placeholder).
- **Audit shared native handles for thread-safety.** A DuckDB connection is not
  safe for concurrent use; concurrent `execute()` on one connection corrupted the
  native heap and aborted the whole process (SIGABRT). Fixed for `WorkspaceSession`
  with a per-session lock, but every shared native handle (DuckDB connections/
  cursors, pyarrow buffers, any C-extension client reused across the FastAPI
  threadpool or background threads) must be audited: serialize with a lock, use a
  per-thread cursor/connection, or make it request-scoped. Add a concurrency
  stress test to CI so a regression aborts the build, not the app.

**Exit = MVP Complete:**

- A new user completes the full golden scenario without touching the Databricks UI after authentication.
- A 1–2 GiB checkout creates no plaintext dataset files on disk.
- The app can restart during checkout and recover.
- All agreed visual transformation steps run locally.
- A reviewed mutation set is merged back to a Delta table and a new table version is recorded.
- The watched-folder path can surface an XLSX file and use it in a join.

---

## Phase 9 — Non-Engineer UX Pass

**Goal:** the product works for the PRD's actual persona — an Excel-comfortable analyst or finance/ops user with no SQL and no Databricks knowledge. This phase closes gaps found by dogfooding Phases 1–7: some are polish, some are PRD requirements (§16, §17, §8.9) that got simplified during build and need to come back.

**Why now:** the golden loop (checkout → transform → validate → commit) is functionally complete and live-verified. This phase doesn't add new capabilities — it makes the existing ones usable without engineering knowledge.

### Epic AA — Onboarding & Setup

**P9.1 — First-run setup screen**
As a new user, I configure the app without touching env vars or JSON files.
- In-app form: workspace URL, warehouse picker (populated via `warehouse_list` after sign-in), test-connection button. Writes `config.json`.
- **AC:** a user with zero prior setup can reach a signed-in Home screen using only in-app UI.

**P9.2 — Friendly auth/connection errors**
- Translate raw OAuth/network errors into actionable text ("Couldn't reach your Databricks workspace — check the URL" vs "Sign-in was cancelled").
- **AC:** each known failure mode (bad URL, cancelled consent, network timeout) shows a distinct, non-technical message.

**P9.3 — Golden-path guided walkthrough**
- Optional first-launch tour on `samples.nyctaxi.trips`: checkout → filter → formula → validate → publish-as-new-table, narrated in plain language.
- **AC:** a new user can complete the tour in under 5 minutes without external help.

### Epic AB — Browse & Checkout

**P9.4 — Catalog/table search**
- Search box over catalogs/schemas/tables by name; hide `information_schema`/system noise by default.
- **AC:** typing a table name jumps to it without manual tree expansion.

**P9.5 — Table preview before checkout**
- Selecting a table shows columns + types + a 5-row sample before the user commits to checkout.
- **AC:** preview loads without starting a checkout operation.

**P9.6 — Recents/favorites**
- Track and surface the user's most-used tables on the browser screen.
- **AC:** a previously checked-out table appears in a "Recent" section.

**P9.7 — Estimated size before checkout**
- Run a remote `COUNT(*)` with the chosen filters before checkout; show "≈128k rows · this may take a minute" (PRD §7.3).
- **AC:** estimate appears before the user clicks Checkout, using the same filters that will run.

**P9.8 — Filter value suggestions**
- For selected columns, fetch distinct values remotely into a dropdown instead of free text.
- **AC:** choosing a filter value from the list, not typed, eliminates zero-row typo failures for suggested columns.

**P9.9 — Row identifier selection at checkout with auto-suggest**
- Move row-key selection into the checkout flow (PRD §16); probe candidate columns for remote uniqueness and pre-select the best match.
- **AC:** user picks (or accepts the suggested) row key before checkout completes, not after.

**P9.10 — Typed filter inputs**
- Date columns get a date picker, numeric columns a number input, driven by known column type.
- **AC:** a date filter cannot be submitted as an unparseable string.

### Epic AC — Grid & Data Understanding

**P9.11 — Column header filter (Excel AutoFilter style)**
- Funnel icon per header → distinct values with checkboxes; compiles to a transient WHERE or "Add as step" (PRD §11).
- **AC:** filtering from a header narrows the grid without requiring the Filter modal.

**P9.12 — Column stats popover**
- Click a header for nulls %, distinct count, min/max, top values (one cheap DuckDB query per column).
- **AC:** stats popover opens in under 1s for a loaded workspace.

**P9.13 — Grid-level search**
- Search box filters visible rows across columns (`ILIKE`).
- **AC:** typing a value highlights/filters matching rows without a new pipeline step.

**P9.14 — Column type icons**
- Small type indicator (date/text/number) in each header.
- **AC:** icon matches the column's DuckDB type.

**P9.15 — Manual cell editing (overlay step)**
As a user, I click a cell and type a corrected value, same as Excel.
- Wire the deferred manual-edit overlay: edits stored keyed by row id, applied as a pipeline step, never mutate source partitions (PRD §8.8).
- **AC:** editing 20 cells produces 20 tracked changes visible in the diff; original checkout data is untouched on disk.

### Epic AD — Transformation Usability

**P9.16 — Plain-language step vocabulary**
- Rename UI labels: "Remove duplicates", "New calculated column", "Find & replace values", "Keep/remove rows" — replacing SQL-flavored terms.
- **AC:** no modal or pipeline-panel label uses a raw SQL/engine term.

**P9.17 — Live before/after preview in transform modals**
- Show row-count delta and 5 sample result rows while configuring a step, before Apply.
- **AC:** changing a filter/formula/etc. condition updates the preview without leaving the modal.

**P9.18 — Formula builder with column chips**
- Clickable column chips + function palette instead of raw expression typing; friendly validation ("no column called `revnue` — did you mean `revenue`?").
- **AC:** a user can build `revenue - cost` without typing backticks or knowing SQL syntax.

**P9.19 — Replace-values fed by actual distinct values**
- "From" side of Replace Values populated from the column's real distinct values (checkbox list), not typed from memory.
- **AC:** standardizing a column's values never requires the user to type a source value.

**P9.20 — Step reordering and enable/disable**
- Drag to reorder steps; toggle a step's `enabled` flag (already in the backend model) without deleting it.
- **AC:** disabling a step excludes it from compilation but preserves its config; reordering recompiles downstream.

**P9.21 — Undo / remove-last-step**
- At minimum, one-click "remove last step"; stretch: pipeline history snapshots.
- **AC:** undoing a step restores the prior pipeline state and grid.

### Epic AE — Validation Usability

**P9.22 — Show failing rows in the grid**
- Clicking a rule's failure count filters the grid to those rows with the failing column highlighted (backend already returns `failed_rows`).
- **AC:** the failed-rows view matches the rule's reported invalid count.

**P9.23 — Plain-language rule summaries**
- Render each rule as a sentence: "revenue must be greater than 0 (error — blocks publish)".
- **AC:** every rule kind has a human-readable summary template.

**P9.24 — Suggested rules from data profiling**
- Surface low-confidence suggestions (e.g. "3 rows in `email` have no `@` — add a check?").
- **AC:** at least one suggestion type ships and can be accepted with one click.

### Epic AF — Commit / Publish Usability

**P9.25 — Reframe commit as "Publish" in plain language**
- Two named choices up front: "Update the original table" (value changes only) vs "Save as a new table" (everything, including added columns) — one explanatory sentence each.
- **AC:** no commit-flow screen uses "MERGE", "row key", or "Delta version" without a plain-language gloss.

**P9.26 — Destination picker instead of free text**
- Browse writable catalogs/schemas via dropdowns; type only the table name. Grey out or hide known-read-only catalogs (`samples`, `system`) where detectable.
- **AC:** selecting a destination never requires typing a fully-qualified table path from memory.

**P9.27 — Pre-commit plain-language summary**
- "You're about to update 1 row and add 0 rows in `customers`. 24 rows unchanged." — sourced from the existing diff computation.
- **AC:** the summary's numbers match the diff modal's counts exactly.

**P9.28 — Enforce validation as a blocking commit gate**
- Blocking (`error`-severity) validation failures must block commit automatically, not just when the user happens to open the Validate modal (PRD §17).
- **AC:** an unresolved blocking failure prevents reaching Ready-to-Commit, with a link back to the failing rows.

**P9.29 — Post-commit deep link**
- "✓ Published" confirmation links to the table in the Databricks UI.
- **AC:** link opens the correct catalog/schema/table page.

**P9.30 — Refresh & Reapply on conflict**
- One-click: re-checkout the source, re-run the declarative pipeline, reapply the manual-edit overlay by row key, flag orphaned edits, re-validate and re-diff (PRD §16.2).
- **AC:** a conflicted commit can be resolved end-to-end without the user manually rebuilding the pipeline.

### Epic AG — Housekeeping & Trust

**P9.31 — Workspace management: rename, delete, duplicate**
- Delete with confirmation; rename; duplicate a workspace's pipeline onto a fresh checkout.
- **AC:** deleting a workspace removes its encrypted data and manifest; Home no longer lists it.

**P9.32 — Commit/checkout history panel**
- Surface the commit journal already recorded in the manifest: "Published 25 rows to X · yesterday".
- **AC:** history reflects every commit for a workspace in chronological order.

**P9.33 — Long-operation progress honesty**
- Stream statement state text during long MERGEs/CREATEs instead of a bare spinner.
- **AC:** the committing screen's message changes at least once during a multi-second commit.

**P9.34 — Error-language pass**
- Every raw DuckDB/Databricks error reaching the UI is translated or prefixed with an actionable hint, across filter/formula/checkout/join — not just commit (already done there).
- **AC:** no raw stack trace or engine error string reaches the UI unprefixed.

**Phase 9 done when:** a non-technical user (PRD persona) completes the full golden scenario — sign in, find a table, checkout with a sensible row key, transform using plain-language steps with live previews, validate and see failing rows, publish to a self-service-chosen destination — without needing SQL knowledge, env-var configuration, or engineer assistance.

---

## Phase 10 — Local Natural-Language SQL Assistant

**Source doc:** `PRD_Technical_Local_SQL_AI_Assistant.docx`.

**Goal:** let an analyst ask a plain-English question against the currently selected pipeline step's data and get back a result grid + the exact DuckDB SQL that produced it — model runs fully on-device, zero data egress, generated SQL is untrusted input until validated.

**Core principle:** the LLM proposes SQL; the application alone decides whether it is safe, valid, and executable. Exploration is ephemeral and never mutates the workspace unless the user explicitly promotes it to a durable pipeline step.

### Epic AH — Model Lifecycle

**P10.1 — Model manifest + download**
- Ship a pinned manifest (`saadxsalman/SS-350M-SQL-Strict-GGUF`, `SS-350M-SQL-Strict.Q8_0.gguf`, ~379 MB, Apache-2.0, SHA-256 `9d1b6a0535...`). Download to `*.partial`, verify byte size + SHA-256, atomic rename into the model directory. Fail closed on any mismatch.
- **AC:** a corrupted or truncated download never becomes an activated model; a fresh install requires explicit user/admin opt-in.

**P10.2 — Model install/uninstall/update controls + enterprise pre-bundle**
- Settings UI: install, verify, remove, show installed version. Support an admin-managed pre-placed GGUF with expected checksum (offline/enterprise path).
- **AC:** uninstalling removes the GGUF and stops any running sidecar; a pre-bundled model activates without a network call.

**P10.3 — Local benchmark smoke test gate**
- A newly downloaded (or pre-bundled) model must pass a small local smoke test (few known prompts → expected SQL shape) before activation.
- **AC:** a model that fails the smoke test is not offered to the exploration UI.

### Epic AI — Inference Runtime

**P10.4 — llama.cpp sidecar lifecycle**
- Launch `llama-server` bound to `127.0.0.1` only, ephemeral port, random per-launch API key kept in memory, `--ctx-size 4096`, `--n-predict 256`. Poll `/health` for readiness (503 loading → 200 ready). Kill child on app exit or feature disable.
- **AC:** `lsof`/network inspection shows no listener on any non-loopback interface; killing the app kills the sidecar (no orphan process).

**P10.5 — Lazy load / idle unload**
- Start the sidecar on first AI question; keep warm while active; stop after 10 minutes idle if memory pressure is high; prefer unloading the model over destabilizing DuckDB under memory pressure.
- **AC:** sidecar process is absent until the first question is asked in a session.

### Epic AJ — Prompt, Generation, Validation

**P10.6 — Schema-only prompt builder**
- Build the deterministic system+user prompt from the selected pipeline step's `current` relation (name always `current`, columns + DuckDB types, read-only/single-statement rules, the question). No other table schemas, no row data by default.
- **AC:** prompt byte-for-byte reproducible for identical (schema, question) input; snapshot-tested.

**P10.7 — Optional safe enrichment (stats, low-cardinality values, row samples)**
- Behind settings flags: numeric min/max (default on), date min/max (default on), low-cardinality distinct values ≤50 (default off). Never dump full columns.
- **Row-sample enrichment default changed from the PRD's "off":** since the model is fully local (no egress), a small evenly-spaced sample (`ai_sql.sample_rows_block`, ≤5 rows, ≤40 chars/cell, ≤1,200-char block, regardless of table size) is included by default to improve accuracy — bounded so a huge/wide table never blows the 4,096-token context.
- **AC:** the sample block is always capped independent of source row/column count; with sampling disabled the prompt contains zero data values — schema and question only.

**P10.8 — Deterministic generation call**
- `temperature=0`, `max_tokens=256`, `stream=false`, single OpenAI-compatible chat-completions call to the sidecar with the random per-launch bearer key.
- **AC:** identical prompt → identical generated SQL across repeated calls (temperature 0 determinism check).

**P10.9 — AST allowlist validator (`sqlparser-rs`, DuckDB dialect)**
- Strip accidental Markdown fences. Parse with the DuckDB dialect. Reject: anything but one `Query`/`SELECT`/`WITH...SELECT` statement; any relation other than `current` or same-query CTEs; any mutating/attach/pragma/install/load/export/import statement; file/network/table functions (`read_csv`, `read_parquet`, `read_json`, `glob`, extension/network calls).
- **AC:** the full §19.2 security corpus (attach db, install extension, export to file, delete rows, read_parquet(url), PRAGMA database_list, information_schema, filesystem paths) is blocked or returned as unsupported — 100% pass required before this story is done.

**P10.10 — Restricted DuckDB execution connection**
- Dedicated connection distinct from the main workspace connection: `allow_community_extensions=false`, `autoinstall_known_extensions=false`, `autoload_known_extensions=false`, `enable_external_access=false` (or filesystem restricted to the workspace path), `lock_configuration=true` after setup. No Unity Catalog credentials reachable from this connection.
- **AC:** a query attempting to read outside the registered `current` relation fails at the DuckDB layer even if it somehow passed AST validation (defense in depth).

**P10.11 — EXPLAIN-before-execute + row/limit guards**
- Run `EXPLAIN` (parser/binder check) before executing. Auto-add `LIMIT` to row-returning queries without an explicit limit; leave aggregates unforced. Cap UI result rows at 10,000 with a prompt to filter/export instead.
- **AC:** a binder error (bad column name) is caught by `EXPLAIN`, never surfaces as a raw crash or partial result.

### Epic AK — Repair Loop

**P10.12 — Single execution-guided repair attempt**
- On parser/binder failure only (never on policy block), build a repair prompt containing the original question, schema, rejected SQL, and the exact DuckDB error; regenerate once at temperature 0; re-validate + re-`EXPLAIN`; execute on success, else surface a user-facing error. Maximum one repair attempt, ever.
- **AC:** a query that fails twice in a row shows a clear "couldn't answer this" message, not a second silent retry.

### Epic AL — Exploration UX

**P10.13 — Ask bar + result pane + SQL disclosure**
- "Ask this data" input on the workspace screen, scoped to the currently selected pipeline step. Result grid + visible generated SQL (with copy) for every successful answer. "Enable Local AI" call-to-action in place of the ask bar when the model isn't installed, instead of a hard failure.
- **AC:** every successful exploration result shows its exact SQL; nothing executes silently.

**P10.14 — Cancellation**
- Cancel button stops both in-flight inference and the DuckDB execution/EXPLAIN for that request.
- **AC:** cancelling mid-inference releases the sidecar for the next question within one UI tick after backend confirms.

**P10.15 — Blocked/invalid/unsupported states**
- Policy-blocked requests show "This request cannot run in Local AI exploration" (not a stack trace). Semantically unanswerable questions return `SELECT 'UNSUPPORTED_REQUEST' AS _error` per the prompt contract and render as a plain "couldn't answer that" state — never a fabricated number.
- **AC:** no raw DuckDB/parser error string or Python traceback reaches this UI unprefixed.

### Epic AM — Promotion to Pipeline

**P10.16 — "Keep as transformation"**
- Promote a successful, still-valid exploration into a durable `sql_transform` pipeline step (re-validated against the same AST policy at save time). Store the validated SQL, `source_step_id`, `model_id`, and the original question as descriptive (non-executable) metadata.
- **AC:** after promotion, reopening/rerunning the pipeline reproduces the step's output with the local AI model neither installed nor running.

**P10.17 — Promotion gating + enterprise policy**
- Respect `allow_promotion_to_pipeline` in the enterprise policy object; alpha rollout ships with promotion disabled, private beta enables it.
- **AC:** with the flag off, "Keep as transformation" is hidden, not just disabled-and-clickable.

### Epic AN — Telemetry, Benchmark, Hardening

**P10.18 — Privacy-preserving telemetry**
- Record only: enabled/disabled, model version, inference/execution latency, token counts, validation result category (valid/parse error/policy blocked/binder error), repair attempted/succeeded, result row count, promotion rate. Never transmit raw questions, schemas, generated SQL, table/column names, or result values.
- **AC:** telemetry payloads audited to contain zero of the excluded fields.

**P10.19 — Benchmark suite (250+ cases) + accuracy gate**
- Build the labeled benchmark across filter/aggregate/derived-metric/window/complex-predicate/ambiguous/security categories (Appendix C weights) across ≥10 synthetic schemas. Primary metric is execution correctness against DuckDB, not string match.
- **AC:** ≥90% overall execution accuracy, ≥95% on simple/medium cases after one repair attempt — required before public beta (AC-10).

**P10.20 — Feature flags + admin disable**
- `local_ai.enabled`, `.model_download`, `.include_stats`, `.include_sample_values`, `.auto_repair`, `.promote_to_pipeline`, `.runtime_sidecar` — all independently toggleable; enterprise admin can force-disable globally.
- **AC:** flipping `local_ai.enabled` off at runtime tears down any running sidecar and hides the ask bar without an app restart.

**Phase 10 done when:** AC-01 through AC-11 (PRD §21) all hold — offline operation after install, zero outbound calls during inference/execution, security corpus 100% blocked, every result shows its SQL, one repair attempt max, promotion reruns without the model, cancel works, and the 250-case benchmark clears the accuracy gate.

---

## Phase 11 — Exploration-First Workflow System

**Source doc:** `Technical PRD — Exploration-First Workflow System v1.0`.

**Goal:** stop exposing `sql_transform` / `branches from Source` / node concepts before a user has decided anything is worth keeping. Split the workspace into three intentions — **Data** (inspect), **Explore** (investigate, ephemeral), **Workflow** (durable, reproducible) — with exploration promoted into workflow steps only on deliberate user action.

**Already shipped (prior turn) — do not re-do:**
- Data / Explore / Workflow tab shell exists in `App.tsx` (`workspaceTab` state, `setWorkspaceTab`), Data tab always shows the pipeline's final output, Workflow tab has the existing pipeline sidebar + DAG input picker.
- The local-AI ask bar already lives only in the Explore tab, and a question **never** auto-creates a workflow step — `sql_transform` steps are created solely via explicit "Keep as transformation" / "Save changes" (`ai_promote`), matching PRD §41/§48's promotion boundary already.
- What's *not* yet built: everything below — AnalysisSpec, non-AI visual exploration (group/measure/filter/pivot), column profiling panel, Saved Answers, ExploreSession/history, temporary-filter chips distinct from workflow, human-readable step naming, deterministic visualization selection.

### Epic AO — ExploreSession & Temporary Filters (PRD §7.2, §16, §35–37)

**P11.1 — `ExploreSession` state object**
- Ephemeral, per-workspace: `{ session_id, active_step_id, current_analysis, transient_filters, history[], current_index }`. Never increments `workflow_revision`.
- **AC:** closing/reopening a workspace clears the session; workflow_revision is provably untouched by any Explore-only action.

**P11.2 — Temporary filter chips (Data tab)**
- `gender = Female ×` style chips above the grid; `Viewing 18,492 / 356,176 rows` counter. Distinct visual treatment (dotted/light) from workflow step cards (solid/checkmarked) per §34.
- **AC:** a temporary filter changes grid rows immediately but produces zero pipeline steps; `[Save filters to workflow]` is the only path that creates one.

**P11.3 — Explore history (back/forward)**
- Each significant AnalysisSpec change pushes a history entry; Cmd/Ctrl+Z / Shift+Z step through it (§16, §57).
- **AC:** navigating history never touches `workflow_revision` or the saved pipeline.

### Epic AP — Column Profiling (PRD §8, Phase 2 of source doc)

**P11.4 — Categorical column profile**
- Panel: type, row count, distinct count, null %, top values with counts/percentages (§8.1). Backend: `profile.column()` using the categorical DuckDB query from §8.3.
- **AC:** opening a profile on a >100k-row local table returns in <1s (perf target §44).

**P11.5 — Numeric column profile**
- Min/median/mean/P95/max/null count via `QUANTILE_CONT` (§8.2/§8.3).
- **AC:** matches the numeric profiling SQL in §8.3 exactly (no approximation without labeling it as such).

**P11.6 — Profile → filter / group-by shortcuts**
- `[Filter]`, `[Group by]`, `[View rows]` on categorical; `[Distribution]`, `[Show outliers]`, `[Filter range]` on numeric — all route into the AnalysisSpec, not into a workflow step.

### Epic AQ — AnalysisSpec & Visual Query Builder (PRD §10–14, Phase 3)

**P11.7 — `AnalysisSpec` schema + `AnalysisCompiler`**
- `{ source, dimensions[], measures[{column,aggregation}], filters[], sort[], limit }` → parameterized DuckDB SQL against `current` (§10, §42). All identifiers validated against the active step's schema.
- **AC:** identical AnalysisSpec compiles to byte-identical SQL (deterministic, snapshot-tested) — mirrors the P10.6 determinism bar already set for the AI prompt builder.

**P11.8 — Group/Measure/Filter/Sort builder UI**
- No-SQL construction of an AnalysisSpec (§11); updates results interactively.
- **AC:** every field reachable through the builder without opening SQL or invoking AI (Acceptance §60 "Explore" bullets 1–3).

**P11.9 — Editable analytical tokens**
- `[age > 60] [group: gender] [avg: claim_amount]` chips, each independently editable, re-running DuckDB without another model call (§12).
- **AC:** editing one token never triggers `ai_ask`/inference — pure AnalysisSpec→SQL recompile.

**P11.10 — Pivot / group builder**
- Rows × Columns × Values × Aggregation → grouped table (§13). Table rendering is sufficient for MVP, no true pivot-grid.

**P11.11 — Direct manipulation + drill-down**
- Clicking a result value offers Filter to/Exclude, Break down by, Compare, View underlying rows (§14); drill-down rewrites the AnalysisSpec (§15).

**P11.12 — Query cancellation**
- New exploration request cancels the previous in-flight one where safe (§43) — extends the same best-effort cancellation groundwork from P10.14.

### Epic AR — Answers (PRD §5.2, §22–25, Phase 4)

**P11.13 — `Answer` object + `AnswerService`**
- `{ answer_id, workspace_id, source_dataset_id, analysis_spec, generated_sql, result_schema, result_preview, visualization_type, created_at, saved }`.

**P11.14 — Deterministic visualization selection (§23)**
- 1 aggregate → KPI card; 1 categorical dim + 1 measure → bar; 1 temporal dim + 1 measure → line; 2+ dims → table; raw rows → grid. User can override. No AI in this decision.

**P11.15 — Save Answer / Saved Answers list**
- Persists `{answer_id, name, analysis_spec, sql, visualization, source_workflow_revision, created_at}` (§25); does not touch the pipeline.
- **AC:** a Saved Answer created against workflow revision N still renders correctly (or clearly flags staleness) after the workflow moves to revision N+1.

### Epic AS — Promote to Workflow (PRD §26, §28–29, Phase 5)

**P11.16 — "Keep as workflow" classification + confirmation dialog**
- Classifies the Answer as filter/aggregation/transform/derived dataset; shows Operation/Input/Output-row-count preview before adding (§26). Reuses existing `ai_promote`-style step-append plumbing, generalized beyond `sql_transform` to cover AnalysisSpec-originated steps too.
- **AC:** ≤2 interactions from a finished Answer to a durable step (Success Metrics §59).

**P11.17 — Human-readable step names**
- `sql_transform` → e.g. "Patients over age 60"; `join_file` → "Join Provider Mapping"; `replace` → "Normalize Diagnosis" (§28). Auto-derive a default name from the AnalysisSpec/step config; user can rename.
- **AC:** Workflow tab never shows a raw step-type identifier unless "Advanced technical details" is explicitly opened (§27).

**P11.18 — Branch de-emphasis in default UI**
- Default: "This step uses Source as its input" (plain sentence). The existing `⤷ branches from X` tree UI (Phase 10 batch) moves behind a "View dependencies" toggle, off by default (§32).

### Epic AT — SQL Explore (PRD §17–18, Phase 6)

**P11.19 — SQL scratchpad tab within Explore**
- Reuses `ai_run_sql`'s validate+execute path (already built) directly from a dedicated "SQL" mode in Explore, no natural-language step required. `[Run] [Edit SQL] [Save Answer] [Keep as workflow]`.
- **AC:** identical security corpus from P10.9 applies unchanged — this is the same validator, just a different entry point.

### Epic AU — Local AI as Optional Explore Mechanism (PRD §19–21, §46–48, Phase 7)

**P11.20 — NL → AnalysisSpec (preferred path)**
- Local model's primary target becomes AnalysisSpec JSON, not raw SQL (§19, §47) — compiled deterministically by `AnalysisCompiler` (P11.7), same "translator not execution authority" boundary as Phase 10.
- **AC:** a NL question that maps to dimensions/measures/filters never touches the SQL-generation path at all.

**P11.21 — NL → SQL fallback**
- For requests too complex for AnalysisSpec, fall back to the existing Phase 10 `ai_ask`/repair-loop/validator pipeline unchanged (§20).

**P11.22 — Graceful AI failure state**
- "Could not interpret this analysis. Try: choosing fields manually / opening SQL / rephrasing." — never a dead end (§48).

**Phase 11 done when:** the §60 acceptance criteria hold — Data/Explore/Workflow fully separated; grouping/measures/pivot/SQL all work with zero AI dependency; every analysis becomes an Answer that does *not* auto-create a workflow step; `workflow_revision` changes only on deliberate add/remove/reorder/edit; commit/diff/validate always operate on durable workflow output, never on transient Explore state (§50 invariant).

---

## Phase 12 — DuckDB-Native Analytical Engine

**Source doc:** `DuckDB_Native_Analytical_Engine_Technical_Design` v1.0.

**Goal:** grow Phase 11's `AnalysisSpec` (5 fields: dimensions/measures/filters/sort/limit) into the doc's full canonical `ExplorePlan` IR — a typed operator sequence (Project, Derive, Filter, Distinct, Join, Aggregate, Having, Window, Qualify, Pivot, Unpivot, SetOp, Order, Limit, Sample, RawSQL) that every interaction surface (direct manipulation, recipes, visual builder, SQL, AI) compiles into the same deterministic DuckDB SQL. The doc is written for a Rust/Tauri single-process engine; this codebase is Python/FastAPI with one DuckDB connection per `WorkspaceSession` (already RLock-serialized, §12.1's "small pool" concern is already satisfied per-workspace) — architecture sections (§2 boundary, §3 topology, §27 Tauri commands) are translated to FastAPI/pydantic terms below, not implemented literally.

**Already shipped, do not re-do:**
- `analysis_spec.py` is the MVP seed of `ExplorePlan` — `AnalysisSpec.filters` = Filter operator (§8.3), `.dimensions`+`.measures` = Aggregate operator (§8.6), `.sort`/`.limit` = Order/Limit (§8.12). Compiles deterministically, parameterized, validated against real columns — matches §10's compiler contract and §11's validation-layer list already for the operators it covers.
- SQL escape hatch (§18, §24.3) — `ai_sql.py`'s AST allowlist + `restricted_connection()` + `explain_and_execute()` already implement the doc's exact defense-in-depth chain (parse → single-statement → relation allowlist → blocked functions → `EXPLAIN` preflight → execute on a restricted connection). §18.1's allowed/blocked statement table is already `ai_sql.SqlPolicyError`'s corpus (Phase 10, P10.9), 100%-tested.
- Promotion (§20) — `promote_analysis_sql()` + `explore_promote_analysis` already do "compile to standalone validated SQL → append as durable step", the P11.16 version of §20.1's `promote()` algorithm.
- Local AI as translator, not execution authority (§19) — already the Phase 10/11 architecture (`ai_runtime.py` never touches DuckDB; validators are authoritative).
- Cancellation/supersession (§14.2) — client-side `AbortController` + generation-style "ignore stale response" already done for Explore (P11.12), though not yet server-side query interrupt (see P12.13).

### Epic BD — Expression AST & Derive (§6, §8.1-8.2)

**P12.1 — `Expression` AST dataclasses**
- `ColumnRef | Literal | Unary | Binary | FunctionCall | CaseWhen | Cast | InList | Between | IsNull | Like` mirroring §6, reusing `formula.py`'s existing safe-expression parsing where it already covers arithmetic (it currently backs the durable `formula` pipeline step — extend/share, don't fork).
- **AC:** an `Expression` compiles to identical SQL whether it originated from a durable `formula` step or an ephemeral Explore `Derive` operator — one code path, per the doc's "one canonical IR" invariant (§2.1).

**P12.2 — Operator-safe function registry (§6.1)**
- Versioned table: function ID → DuckDB rendering → arg types → return type → determinism → capability tag (`scalar_numeric`/`scalar_text`/`date_time`/`aggregate`/`window`/blocked `external_io`/`side_effect`). Reuse `ai_sql.BLOCKED_FUNCTIONS` as the seed for the blocklist side.
- **AC:** the registry, not free-form strings, is the only path from a function name to SQL — visual builder and AI both look functions up here, never emit a name the registry doesn't know.

**P12.3 — `Derive` operator**
- `AnalysisSpec` gains `derive: list[{name, expr}]` compiling to `SELECT *, (expr) AS name` (§8.2), using P12.1's Expression AST (parameterized where the expression contains literals).
- **AC:** a Derive column immediately becomes available as a Filter/Aggregate/Sort target in the same request (matches the durable `formula` step's existing column-availability behavior).

### Epic BE — Distinct, Having, Window, Qualify (§8.4, §8.7-8.9)

**P12.4 — `Distinct` operator** — `SELECT DISTINCT` over selected columns, explicitly not conflated with the existing dedup-by-key `deduplicate` pipeline step (§8.4's important distinction).

**P12.5 — `Having` operator** — post-aggregation filter, same `FilterCond` shape as `Filter` but compiled after `GROUP BY` (§8.7); UI label "Filter summarized results".

**P12.6 — `Window` operator**
- `{fn, args, partition_by, order_by, frame, alias}` (§8.8, Appendix A.4) — `rank/row_number/lag/lead/first/last/sum/avg` over `OVER(...)`. Flag as memory-intensive in the resource-governance UI per §23.2 (windows are blocking/buffering operators).
- **AC:** `lag`/`rank` cover the "Compare periods" and "Top N per partition" recipes (P12.9) without hand-written SQL.

**P12.7 — `Qualify` operator** — filters on window outputs (§8.9) without a synthetic subquery; DuckDB `QUALIFY` natively.

**P12.8 — Multi-stage compiler (CTE staging)**
- `compile_analysis` currently emits one `SELECT`; extend to emit `WITH s0 AS (...), s1 AS (...) SELECT ... QUALIFY ...` when Aggregate precedes Window precedes Qualify (§10.1's worked example). The compiler — not the caller — decides stage boundaries.
- **AC:** §29.1's worked example ("top 5 diagnoses per state") round-trips: `Filter → Aggregate → Window(rank) → Qualify → Order` compiles to the doc's exact 2-CTE shape and returns the right rows against a real checkout.

### Epic BF — Intent Recipes (§9)

**P12.9 — Recipe layer**
- `Summarize / Trend / Top-Bottom-N / Compare periods / Running total / Moving average / Contribution / Duplicates / Missing values / Outliers / Distribution / Reconcile / Unmatched` (§9 table) — each a deterministic, versioned expansion into P12.1-P12.7 operators, never a new engine primitive (§9.1's recipe contract; matches this codebase's existing "explore builder, not new node types" philosophy from Phase 11).
- **AC:** every recipe is inspectable — "open advanced" shows the expanded operator list, matching Phase 11's existing SQL-disclosure pattern (`analysisShowSql`).

### Epic BG — Multi-Relation Explore (§8.5, §13.2-13.3)

**P12.10 — `Join` operator inside Explore**
- Today, joins against local sources are only a *durable* pipeline step (`join_file`). Add an ephemeral Explore-side Join (inner/left/right/full/semi/anti) against any registered source — needed for the Reconcile/Unmatched recipes (§29.3) without forcing a workflow step first.
- **AC:** the anti-join "Unmatched" recipe runs entirely in Explore against an approved local relation; the AI/UI never sees the source's physical file path (§13.3).

### Epic BH — Shape Operators (§8.10-8.13) — Phase 2/4 priority per source doc

**P12.11 — Pivot / Unpivot** — feature-tested against the pinned DuckDB build at startup (§21.2); not assumed universally available.

**P12.12 — SetOp (UNION/INTERSECT/EXCEPT)** — powers the Reconcile recipe's "combine" step (§8.11).

**P12.13 — Sample** — explicit, never silent; every sampled Answer shows a "Sampled/approximate" badge + method/size + one-click "Run exact" (§8.13); sampling is hard-blocked from diff/validate/commit (already true by construction — Explore never touches commit, §50).

### Epic BI — Resource Governance & Cancellation Hardening (§12, §14, §23)

**P12.14 — Server-side query interrupt**
- Client-side `AbortController` (P11.12) stops the *response* from being applied but doesn't stop DuckDB from finishing the work server-side. Wire real interruption — DuckDB Python's `interrupt()` on the connection — so a superseded query actually stops consuming CPU/memory, not just its result.
- **AC:** issuing a new analysis while a large `GROUP BY` is running measurably frees the CPU core within ~1 request cycle, not after the original query finishes.

**P12.15 — Preview vs. full-materialization distinction (§12.4)**
- UI must distinguish "rows shown" from "exact total known" — already partially true (`gridTotal` vs windowed rows) but not yet formalized for Explore's aggregate Answers, which the doc says should generally run full (aggregated cardinality is expected small) rather than windowed-preview.

**P12.16 — Memory/thread policy + resource-limit error UX**
- Product memory ceiling relative to physical RAM; on `ResourceLimit`, offer "filter earlier / sample / reduce columns" per §22/§23.2 — reuse the existing typed-error pattern (`_short_db_error`) but add this specific classification.

### Epic BJ — Caching (§15)

**P12.17 — Explore result/metadata cache**
- Cache key = `(workspace_id, source_revision→pipeline_revision, active_step_id, plan_fingerprint, exact_or_sampled)` (§15.2, §10.3's fingerprint). A workflow mutation invalidates all downstream Explore caches — already true by construction (`state.sessions.evict()` on every pipeline save) but not yet paired with a positive cache for repeat/back-forward navigation (P11.3's history currently re-fetches).
- **AC:** navigating Explore history backward reuses a cached result instead of re-querying DuckDB, measurably (cache-hit metric, P12.18).

### Epic BK — Observability & Testing (§25, §26)

**P12.18 — Engine metrics** — `query.elapsed_ms/compile_ms/first_batch_ms`, `cache.hit/miss`, `operator.recipe_usage`, `sql.escape_hatch_rate`, `ai.plan_success/fallback_sql` (§25.1) — plan/operator metadata only by default, never raw SQL/literals (§25.2), consistent with Phase 10's existing telemetry privacy stance (P10.18).

**P12.19 — Golden scenario suite (§26.2)**
- Filter+derive+aggregate; Top-N-per-partition via rank+qualify; period-over-period via lag; dedupe by stable key+timestamp; anti-join reconciliation; pivot/unpivot round-trip; set-op semantics incl. nulls; decimal precision through calculations; raw-SQL security matrix (already covered by `test_ai_sql.py`'s corpus — extend, don't duplicate).

**P12.20 — DuckDB compatibility CI (§21.2, §26.3)**
- Pin the exact DuckDB version in `requirements.txt` (currently `duckdb==1.1.*` — tighten to an exact pin); a separate CI job runs the full engine suite against the next candidate release before any version bump, never an incidental `pip install --upgrade`.

**Phase 12 done when:** the §30 Definition of Done holds — filter/derive/join/summarize/compare/rank/dedupe/profile/reconcile/sort/limit all work without AI; every visual interaction lowers to a versioned canonical plan; all generated SQL is schema-bound, parameterized, validated, read-only; raw SQL remains a safe relation-scoped escape hatch; results stream in batches with working cancellation; Explore caches are revision-safe; promotion is explicit/previewed/never silently changes grain; an eventual DuckDB version bump is an explicit compatibility project, not an incidental dependency update.

---

## Sequencing Notes

- **Critical path:** Phase 2 (encrypted streaming checkout) is the technical linchpin — OAuth-in-Rust + Arrow external links + encrypted Parquet + resumable journal all converge here. Spike it early, even during Phase 1.
- **Parallelizable:** Phase 3 grid UI can build against mock data while the Phase 2 backend lands. Phase 5 watched folder is largely independent.
- **Cross-cutting from day one:** log redaction, encryption, typed command/event contracts, and the operation journal. Retrofitting these is painful.
- **Keep the workspace layer Databricks-agnostic** (PRD §34) so future adapters (Snowflake, BigQuery, Fabric) slot in later.

## Deferred (post-MVP) — PRD §25 / Tech §25

SQL / Python / Polars steps · AI transformation generation · cost-based local-vs-remote execution router · Change Data Feed incremental refresh · branching DAGs & reusable recipes · automatic watched-folder recipe execution · remote cache revocation / offline leases · endpoint DLP controls · additional governed-platform adapters.

---

## Phase → Tech-Design Milestone Map

| Phase | Tech Design milestone (§24) |
|-------|-----------------------------|
| 0 | M0 — Skeleton |
| 1 | M1 — Databricks read |
| 2 | M2 — Encrypted checkout |
| 3 | M3 — Workspace / grid |
| 4 | M4 — Core transforms |
| 5 | M5 — Local files |
| 6 | M6 — Diff / validation |
| 7 | M7 — Commit |
| 8 | M8 — Hardening |
| 10 | Local SQL AI Assistant M0–M7 (own Tech Design doc) |
| 11 | Exploration-First Workflow System Phases 1–7 (own Tech PRD) |
| 12 | DuckDB-Native Analytical Engine Phases 0–8 (own Tech Design) |
