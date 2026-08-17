//! Typed Tauri command surface (IMPLEMENTATION_PLAN §17).
//!
//! Phase 0 defined the contract with mock bodies; Phase 1 replaces the auth and
//! read-path bodies with real Databricks calls through [`AuthService`] and
//! [`DatabricksClient`]. Commands still return `Result<T, String>` so any failure
//! renders in the UI rather than hanging (P0.3). Checkout/commit/etc. remain
//! stubs until Phase 2+.

use std::time::Duration;

use tauri::{AppHandle, Emitter, State};
use tracing::info;

use crate::auth_service::{AuthError, AuthService};
use crate::dbx_rest::{is_terminal, DatabricksClient};
use crate::dbx_sql::{build_working_set_select, FilterOp, Predicate};
use crate::events;
use crate::logging::new_operation_id;
use crate::model::*;
use crate::workspace;
use crate::AppState;

type CmdResult<T> = std::result::Result<T, String>;

/// Round-trip smoke command (P0.3): UI → Rust → UI with a correlated op id.
#[tauri::command]
pub fn ping(message: String) -> CmdResult<Pong> {
    let operation_id = new_operation_id();
    info!(operation_id, cmd = "ping", "round-trip smoke");
    Ok(Pong {
        message: "pong".into(),
        echoed: message,
        operation_id,
    })
}

// ---- Auth (Epic A) ----

#[tauri::command]
pub async fn auth_connect(state: State<'_, AppState>) -> CmdResult<AuthUser> {
    state.auth.connect().await.map_err(|e| e.to_string())
}

#[tauri::command]
pub fn auth_logout(state: State<'_, AppState>) -> CmdResult<()> {
    state.auth.logout();
    Ok(())
}

#[tauri::command]
pub fn auth_status(state: State<'_, AppState>) -> CmdResult<bool> {
    Ok(state.auth.is_authenticated())
}

#[tauri::command]
pub fn config_get(state: State<'_, AppState>) -> CmdResult<AppConfig> {
    let c = state.auth.config();
    Ok(AppConfig {
        configured: c.is_configured(),
        workspace_url: c.workspace_url,
        client_id: c.client_id,
        warehouse_id: c.warehouse_id,
    })
}

/// Obtain a client with a fresh token, emitting `auth://expired` when the
/// session can no longer be refreshed so the UI can prompt reauth.
async fn client(auth: &AuthService, app: &AppHandle) -> CmdResult<DatabricksClient> {
    match auth.access_context().await {
        Ok(ctx) => Ok(DatabricksClient::new(ctx.base, ctx.token)),
        Err(AuthError::Expired) => {
            let _ = app.emit(events::AUTH_EXPIRED, ());
            Err("session expired; please sign in again".into())
        }
        Err(e) => Err(e.to_string()),
    }
}

// ---- Unity Catalog browsing (Epic B, §7) ----

#[tauri::command]
pub async fn catalog_list(state: State<'_, AppState>, app: AppHandle) -> CmdResult<Vec<Catalog>> {
    let c = client(&state.auth, &app).await?;
    let cats = c.list_catalogs().await.map_err(|e| e.to_string())?;
    Ok(cats.into_iter().map(|x| Catalog { name: x.name }).collect())
}

#[tauri::command]
pub async fn schema_list(
    state: State<'_, AppState>,
    app: AppHandle,
    catalog: String,
) -> CmdResult<Vec<Schema>> {
    let c = client(&state.auth, &app).await?;
    let schemas = c.list_schemas(&catalog).await.map_err(|e| e.to_string())?;
    Ok(schemas
        .into_iter()
        .map(|x| Schema {
            catalog: if x.catalog_name.is_empty() {
                catalog.clone()
            } else {
                x.catalog_name
            },
            name: x.name,
        })
        .collect())
}

#[tauri::command]
pub async fn table_list(
    state: State<'_, AppState>,
    app: AppHandle,
    catalog: String,
    schema: String,
) -> CmdResult<Vec<TableSummary>> {
    let c = client(&state.auth, &app).await?;
    let tables = c
        .list_tables(&catalog, &schema)
        .await
        .map_err(|e| e.to_string())?;
    Ok(tables
        .into_iter()
        .map(|t| TableSummary {
            full_name: if t.full_name.is_empty() {
                format!("{catalog}.{schema}.{}", t.name)
            } else {
                t.full_name
            },
            name: t.name,
            kind: if t.table_type.is_empty() {
                "TABLE".into()
            } else {
                t.table_type
            },
        })
        .collect())
}

#[tauri::command]
pub async fn table_get(
    state: State<'_, AppState>,
    app: AppHandle,
    full_name: String,
) -> CmdResult<TableMetadata> {
    let c = client(&state.auth, &app).await?;
    let t = c.get_table(&full_name).await.map_err(|e| e.to_string())?;
    Ok(TableMetadata {
        full_name: if t.full_name.is_empty() {
            full_name
        } else {
            t.full_name
        },
        columns: t
            .columns
            .into_iter()
            .map(|col| ColumnMeta {
                name: col.name,
                type_name: col.type_text,
                nullable: col.nullable,
            })
            .collect(),
        row_count: None,
    })
}

// ---- Warehouses (Epic C, §8.2) ----

#[tauri::command]
pub async fn warehouse_list(
    state: State<'_, AppState>,
    app: AppHandle,
) -> CmdResult<Vec<WarehouseSummary>> {
    let c = client(&state.auth, &app).await?;
    let whs = c.list_warehouses().await.map_err(|e| e.to_string())?;
    Ok(whs
        .into_iter()
        .map(|w| WarehouseSummary {
            id: w.id,
            name: w.name,
            state: w.state,
        })
        .collect())
}

#[tauri::command]
pub async fn warehouse_get(
    state: State<'_, AppState>,
    app: AppHandle,
    id: String,
) -> CmdResult<WarehouseSummary> {
    let c = client(&state.auth, &app).await?;
    let w = c.get_warehouse(&id).await.map_err(|e| e.to_string())?;
    Ok(WarehouseSummary {
        id: w.id,
        name: w.name,
        state: w.state,
    })
}

#[tauri::command]
pub async fn warehouse_start(
    state: State<'_, AppState>,
    app: AppHandle,
    id: String,
) -> CmdResult<()> {
    let c = client(&state.auth, &app).await?;
    c.start_warehouse(&id).await.map_err(|e| e.to_string())
}

// ---- Statement Execution spike (Epic D, P1.10) ----

/// Run a small projected + filtered SELECT and return an inline preview page.
/// Proves auth + warehouse + parameterized query end-to-end.
#[tauri::command]
pub async fn run_select_spike(
    state: State<'_, AppState>,
    app: AppHandle,
    warehouse_id: String,
    table: String,
    columns: Vec<String>,
    filters: Vec<FilterSpec>,
) -> CmdResult<PreviewPage> {
    let operation_id = new_operation_id();
    let predicates: Vec<Predicate> = filters
        .into_iter()
        .map(|f| {
            let op = FilterOp::parse(&f.op).ok_or_else(|| format!("unknown operator: {}", f.op))?;
            Ok(Predicate {
                column: f.column,
                op,
                value: f.value,
            })
        })
        .collect::<Result<_, String>>()?;

    let compiled =
        build_working_set_select(&table, &columns, &predicates).map_err(|e| e.to_string())?;
    info!(operation_id, cmd = "run_select_spike", table = %table, "submitting parameterized select");

    let c = client(&state.auth, &app).await?;
    let mut resp = c
        .submit_statement(&warehouse_id, &compiled.sql, &compiled.params)
        .await
        .map_err(|e| e.to_string())?;

    // Poll with exponential backoff (1s → 2s → 4s, cap 5s), ~30s budget (§8.5).
    let statement_id = resp.statement_id.clone().unwrap_or_default();
    let mut delay = Duration::from_secs(1);
    let mut waited = Duration::ZERO;
    while !is_terminal(&resp.status.state) {
        if waited >= Duration::from_secs(30) {
            return Err("timed out waiting for statement".into());
        }
        tokio::time::sleep(delay).await;
        waited += delay;
        delay = (delay * 2).min(Duration::from_secs(5));
        resp = c
            .get_statement(&statement_id)
            .await
            .map_err(|e| e.to_string())?;
    }

    if resp.status.state != "SUCCEEDED" {
        let msg = resp
            .status
            .error
            .map(|e| e.message)
            .unwrap_or_else(|| resp.status.state.clone());
        return Err(format!("statement {}: {msg}", resp.status.state));
    }

    let columns = resp.column_names();
    let rows = resp
        .rows()
        .into_iter()
        .map(|row| {
            row.into_iter()
                .map(|cell| {
                    cell.map(serde_json::Value::from)
                        .unwrap_or(serde_json::Value::Null)
                })
                .collect()
        })
        .collect();
    Ok(PreviewPage {
        columns,
        rows,
        offset: 0,
        total: None,
    })
}

// ---- Checkout (stub; real streaming in Phase 2) ----

#[tauri::command]
pub fn checkout_start(spec: CheckoutSpec) -> CmdResult<OperationId> {
    let operation_id = new_operation_id();
    info!(operation_id, cmd = "checkout_start", table = %spec.table, "stub checkout");
    Ok(OperationId { operation_id })
}

#[tauri::command]
pub fn checkout_cancel(operation_id: String) -> CmdResult<()> {
    info!(operation_id, cmd = "checkout_cancel", "stub cancel");
    Ok(())
}

// ---- Workspace / pipeline (Phase 0 fs; pipeline stubs) ----

#[tauri::command]
pub fn workspace_create(name: String) -> CmdResult<WorkspaceSummary> {
    let m = workspace::create(&name).map_err(|e| e.to_string())?;
    info!(workspace_id = %m.workspace_id, cmd = "workspace_create", "created workspace");
    Ok(WorkspaceSummary {
        workspace_id: m.workspace_id,
        name: m.name,
        source_table: m.source.table,
        base_version: m.source.base_version,
        pipeline_revision: m.pipeline_revision,
        row_count: m.storage.row_count,
        logical_bytes: m.storage.logical_bytes,
    })
}

#[tauri::command]
pub fn workspace_open(workspace_id: String) -> CmdResult<WorkspaceSummary> {
    let m = workspace::read_manifest(&workspace_id).map_err(|e| e.to_string())?;
    Ok(WorkspaceSummary {
        workspace_id: m.workspace_id,
        name: m.name,
        source_table: m.source.table,
        base_version: m.source.base_version,
        pipeline_revision: m.pipeline_revision,
        row_count: m.storage.row_count,
        logical_bytes: m.storage.logical_bytes,
    })
}

#[tauri::command]
pub fn workspace_list() -> CmdResult<Vec<String>> {
    workspace::list().map_err(|e| e.to_string())
}

#[tauri::command]
pub fn pipeline_add_step(workspace_id: String, _step: StepSpec) -> CmdResult<PipelineRevision> {
    Ok(PipelineRevision {
        workspace_id,
        pipeline_revision: 1,
    })
}

#[tauri::command]
pub fn pipeline_update_step(
    workspace_id: String,
    _step_id: String,
    _step: StepSpec,
) -> CmdResult<PipelineRevision> {
    Ok(PipelineRevision {
        workspace_id,
        pipeline_revision: 2,
    })
}

// ---- Preview / diff / validation / commit / watcher (stubs) ----

#[tauri::command]
pub fn preview_query(req: PreviewRequest) -> CmdResult<PreviewPage> {
    Ok(PreviewPage {
        columns: vec!["customer_id".into(), "company".into(), "country".into()],
        rows: vec![],
        offset: req.offset,
        total: Some(0),
    })
}

#[tauri::command]
pub fn diff_compute(_workspace_id: String) -> CmdResult<DiffSummary> {
    Ok(DiffSummary {
        added: 0,
        modified: 0,
        deleted: 0,
        unchanged: 0,
    })
}

#[tauri::command]
pub fn validation_run(_workspace_id: String) -> CmdResult<ValidationSummary> {
    Ok(ValidationSummary {
        valid_rows: 0,
        invalid_rows: 0,
        blocking: false,
    })
}

#[tauri::command]
pub fn commit_start(_workspace_id: String, _options: CommitOptions) -> CmdResult<OperationId> {
    let operation_id = new_operation_id();
    info!(operation_id, cmd = "commit_start", "stub commit");
    Ok(OperationId { operation_id })
}

#[tauri::command]
pub fn watcher_add(_folder_path: String) -> CmdResult<WatcherId> {
    Ok(WatcherId {
        watcher_id: new_operation_id(),
    })
}

#[tauri::command]
pub fn watcher_remove(_watcher_id: String) -> CmdResult<()> {
    Ok(())
}
