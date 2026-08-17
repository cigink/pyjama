//! Live read-path smoke check against a real Databricks workspace.
//!
//! Interactive: opens the browser for OAuth U2M sign-in, then exercises
//! catalog listing, warehouse status, and a trivial parameterized statement.
//!
//! Run:
//!   PYJAMA_WORKSPACE_URL=... PYJAMA_WAREHOUSE_ID=... \
//!     cargo run --example live_check

use std::sync::Arc;
use std::time::Duration;

use pyjama_lib::auth_service::AuthService;
use pyjama_lib::config::DatabricksConfig;
use pyjama_lib::dbx_rest::{is_terminal, DatabricksClient};
use pyjama_lib::dbx_sql::{build_working_set_select, FilterOp, Predicate};
use pyjama_lib::keystore::MemoryKeyStore;

#[tokio::main]
async fn main() {
    let config = DatabricksConfig::from_env();
    if !config.is_configured() {
        eprintln!("set PYJAMA_WORKSPACE_URL (and optionally PYJAMA_CLIENT_ID)");
        std::process::exit(2);
    }
    let warehouse_id = std::env::var("PYJAMA_WAREHOUSE_ID").unwrap_or_default();
    println!("workspace: {}", config.workspace_url);
    println!("client_id: {}", config.client_id);

    let svc = AuthService::new(config, Arc::new(MemoryKeyStore::default()));
    println!("\n>>> opening browser for Databricks sign-in… complete consent in the browser");
    let user = svc.connect().await.expect("oauth connect failed");
    println!("signed in ✓  scopes={:?}", user.scopes);

    let ctx = svc.access_context().await.expect("no access context");
    let client = DatabricksClient::new(ctx.base, ctx.token);

    // Epic B — Unity Catalog
    let catalogs = client.list_catalogs().await.expect("list_catalogs failed");
    println!("\ncatalogs ({}): {:?}", catalogs.len(), catalogs.iter().map(|c| &c.name).collect::<Vec<_>>());
    if let Some(first) = catalogs.iter().find(|c| c.name != "system" && c.name != "__databricks_internal") {
        match client.list_schemas(&first.name).await {
            Ok(schemas) => println!("schemas in {}: {:?}", first.name, schemas.iter().map(|s| &s.name).take(10).collect::<Vec<_>>()),
            Err(e) => println!("list_schemas({}) -> {e}", first.name),
        }
    }

    // Epic C — Warehouse
    if !warehouse_id.is_empty() {
        match client.get_warehouse(&warehouse_id).await {
            Ok(w) => println!("\nwarehouse: {} [{}] state={}", w.name, w.id, w.state),
            Err(e) => println!("\nget_warehouse -> {e}"),
        }
    }

    // Epic D — parameterized statement spike
    if !warehouse_id.is_empty() {
        let q = build_working_set_select(
            "system.information_schema.tables",
            &["table_catalog".into(), "table_schema".into(), "table_name".into()],
            &[Predicate { column: "table_schema".into(), op: FilterOp::Eq, value: "information_schema".into() }],
        )
        .expect("build sql");
        println!("\nSQL: {}", q.sql);
        println!("params: {:?}", q.params);

        match client.submit_statement(&warehouse_id, &q.sql, &q.params).await {
            Ok(mut resp) => {
                let sid = resp.statement_id.clone().unwrap_or_default();
                let mut delay = Duration::from_secs(1);
                let mut waited = Duration::ZERO;
                while !is_terminal(&resp.status.state) && waited < Duration::from_secs(60) {
                    tokio::time::sleep(delay).await;
                    waited += delay;
                    delay = (delay * 2).min(Duration::from_secs(5));
                    resp = client.get_statement(&sid).await.expect("poll failed");
                }
                println!("statement state: {}", resp.status.state);
                if resp.status.state == "SUCCEEDED" {
                    println!("columns: {:?}", resp.column_names());
                    for row in resp.rows().iter().take(5) {
                        println!("  {row:?}");
                    }
                    println!("\n✓ LIVE READ PATH OK");
                } else if let Some(err) = resp.status.error {
                    println!("statement error: {}", err.message);
                }
            }
            Err(e) => println!("submit_statement -> {e}"),
        }
    }
}
