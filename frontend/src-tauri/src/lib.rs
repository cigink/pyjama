//! PyJama Rust core.
//!
//! Phase 0 skeleton + Phase 1 Databricks read path. Owns the Tauri app builder,
//! the shared [`AppState`], and wires the typed command surface. The frontend
//! (React/TS) talks to this crate exclusively through [`commands`] and the events
//! named in [`events`].

pub mod auth_service;
pub mod commands;
pub mod config;
pub mod dbx_rest;
pub mod dbx_sql;
pub mod events;
pub mod keystore;
pub mod logging;
pub mod model;
pub mod oauth;
pub mod pkce;
pub mod session;
pub mod workspace;

use std::sync::Arc;

use tracing::info;

use auth_service::AuthService;
use config::DatabricksConfig;
use keystore::OsKeyStore;

/// Shared application state managed by Tauri and injected into commands.
pub struct AppState {
    pub auth: Arc<AuthService>,
}

impl AppState {
    pub fn from_env() -> Self {
        let config = DatabricksConfig::from_env();
        let keystore = Arc::new(OsKeyStore);
        AppState {
            auth: Arc::new(AuthService::new(config, keystore)),
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    logging::init();
    info!(version = env!("CARGO_PKG_VERSION"), "PyJama core starting");

    tauri::Builder::default()
        .manage(AppState::from_env())
        .invoke_handler(tauri::generate_handler![
            commands::ping,
            commands::auth_connect,
            commands::auth_logout,
            commands::auth_status,
            commands::config_get,
            commands::catalog_list,
            commands::schema_list,
            commands::table_list,
            commands::table_get,
            commands::warehouse_list,
            commands::warehouse_get,
            commands::warehouse_start,
            commands::run_select_spike,
            commands::checkout_start,
            commands::checkout_cancel,
            commands::workspace_create,
            commands::workspace_open,
            commands::workspace_list,
            commands::pipeline_add_step,
            commands::pipeline_update_step,
            commands::preview_query,
            commands::diff_compute,
            commands::validation_run,
            commands::commit_start,
            commands::watcher_add,
            commands::watcher_remove,
        ])
        .run(tauri::generate_context!())
        .expect("error while running PyJama");
}
