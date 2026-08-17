//! Typed contracts shared across the Tauri command boundary.
//!
//! Phase 0 defines the *shape* of the domain (mirroring IMPLEMENTATION_PLAN §17
//! and §18); later phases fill in real behavior. Every type is `Serialize +
//! Deserialize` so it crosses to the TypeScript frontend unchanged.

use serde::{Deserialize, Serialize};

// ---- Auth (Epic-A shape; real flow lands Phase 1) ----

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuthUser {
    pub workspace_url: String,
    pub user_subject: String,
    pub scopes: Vec<String>,
}

// ---- Unity Catalog browsing ----

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Catalog {
    pub name: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Schema {
    pub catalog: String,
    pub name: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TableSummary {
    pub full_name: String,
    pub name: String,
    pub kind: String, // "TABLE" | "VIEW"
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ColumnMeta {
    pub name: String,
    pub type_name: String,
    pub nullable: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TableMetadata {
    pub full_name: String,
    pub columns: Vec<ColumnMeta>,
    pub row_count: Option<u64>,
}

// ---- Checkout / workspace ----

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FilterSpec {
    pub column: String,
    pub op: String,
    pub value: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CheckoutSpec {
    pub workspace_url: String,
    pub table: String,
    pub columns: Vec<String>,
    pub filters: Vec<FilterSpec>,
    pub row_key: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OperationId {
    pub operation_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkspaceSummary {
    pub workspace_id: String,
    pub name: String,
    pub source_table: String,
    pub base_version: u64,
    pub pipeline_revision: u64,
    pub row_count: u64,
    pub logical_bytes: u64,
}

// ---- Pipeline ----

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StepSpec {
    pub id: String,
    pub ordinal: u32,
    #[serde(rename = "type")]
    pub kind: String,
    pub enabled: bool,
    pub config: serde_json::Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PipelineRevision {
    pub workspace_id: String,
    pub pipeline_revision: u64,
}

// ---- Preview ----

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SortSpec {
    pub column: String,
    pub direction: String, // "asc" | "desc"
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PreviewRequest {
    pub workspace_id: String,
    pub step_id: String,
    pub offset: u64,
    pub limit: u64,
    #[serde(default)]
    pub sort: Vec<SortSpec>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PreviewPage {
    pub columns: Vec<String>,
    pub rows: Vec<Vec<serde_json::Value>>,
    pub offset: u64,
    pub total: Option<u64>,
}

// ---- Diff / validation / commit ----

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DiffSummary {
    pub added: u64,
    pub modified: u64,
    pub deleted: u64,
    pub unchanged: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ValidationSummary {
    pub valid_rows: u64,
    pub invalid_rows: u64,
    pub blocking: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CommitOptions {
    pub target_table: String,
    pub create_new: bool,
}

// ---- App config (non-secret) ----

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppConfig {
    pub workspace_url: String,
    pub client_id: String,
    pub warehouse_id: Option<String>,
    pub configured: bool,
}

// ---- Warehouses ----

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WarehouseSummary {
    pub id: String,
    pub name: String,
    pub state: String,
}

// ---- Watched folder ----

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WatcherId {
    pub watcher_id: String,
}

// ---- Round-trip smoke (P0.3) ----

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Pong {
    pub message: String,
    pub echoed: String,
    pub operation_id: String,
}
