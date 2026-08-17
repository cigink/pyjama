//! Databricks REST client (Phase 1 — Epics B, C, D).
//!
//! Covers the read path: Unity Catalog browsing (§7), SQL warehouse
//! lifecycle (§8.2), and the Statement Execution spike (§8.4/§8.5) using inline
//! JSON results for small `SELECT`s. Phase 2 adds `ARROW_STREAM` +
//! `EXTERNAL_LINKS` streaming on top of the same client.
//!
//! Construction takes the base URL and access token explicitly so tests can
//! point it at a mock server. The bearer token is attached per request and never
//! logged; presigned/external URLs are out of scope until Phase 2.

use serde::{Deserialize, Serialize};
use url::Url;

use crate::dbx_sql::StatementParam;
use crate::logging::Secret;

pub struct DatabricksClient {
    base: Url,
    access_token: Secret,
    http: reqwest::Client,
}

#[derive(Debug, thiserror::Error)]
pub enum RestError {
    #[error("http error: {0}")]
    Http(String),
    #[error("databricks api {status}: {message}")]
    Api { status: u16, message: String },
    #[error("statement failed: {0}")]
    StatementFailed(String),
    #[error("timed out waiting for statement")]
    Timeout,
}

impl DatabricksClient {
    pub fn new(base: Url, access_token: Secret) -> Self {
        DatabricksClient {
            base,
            access_token,
            http: reqwest::Client::new(),
        }
    }

    fn url(&self, path: &str) -> Url {
        self.base.join(path).expect("valid static path")
    }

    async fn get_json<T: for<'de> Deserialize<'de>>(
        &self,
        path: &str,
        query: &[(&str, &str)],
    ) -> Result<T, RestError> {
        let mut req = self
            .http
            .get(self.url(path))
            .bearer_auth(self.access_token.expose());
        if !query.is_empty() {
            req = req.query(query);
        }
        let resp = req
            .send()
            .await
            .map_err(|e| RestError::Http(e.to_string()))?;
        Self::parse_json(resp).await
    }

    async fn post_json<B: Serialize, T: for<'de> Deserialize<'de>>(
        &self,
        path: &str,
        body: &B,
    ) -> Result<T, RestError> {
        let resp = self
            .http
            .post(self.url(path))
            .bearer_auth(self.access_token.expose())
            .json(body)
            .send()
            .await
            .map_err(|e| RestError::Http(e.to_string()))?;
        Self::parse_json(resp).await
    }

    async fn post_empty(&self, path: &str) -> Result<(), RestError> {
        let resp = self
            .http
            .post(self.url(path))
            .bearer_auth(self.access_token.expose())
            .header("content-length", "0")
            .send()
            .await
            .map_err(|e| RestError::Http(e.to_string()))?;
        if resp.status().is_success() {
            Ok(())
        } else {
            Err(Self::api_error(resp).await)
        }
    }

    async fn parse_json<T: for<'de> Deserialize<'de>>(
        resp: reqwest::Response,
    ) -> Result<T, RestError> {
        if resp.status().is_success() {
            resp.json::<T>()
                .await
                .map_err(|e| RestError::Http(e.to_string()))
        } else {
            Err(Self::api_error(resp).await)
        }
    }

    async fn api_error(resp: reqwest::Response) -> RestError {
        let status = resp.status().as_u16();
        let message = resp
            .json::<ApiError>()
            .await
            .map(|e| e.message)
            .unwrap_or_else(|_| "unknown error".to_string());
        RestError::Api { status, message }
    }

    // ---- Unity Catalog browsing (§7) ----

    pub async fn list_catalogs(&self) -> Result<Vec<CatalogInfo>, RestError> {
        let r: CatalogList = self
            .get_json("/api/2.1/unity-catalog/catalogs", &[])
            .await?;
        Ok(r.catalogs.unwrap_or_default())
    }

    pub async fn list_schemas(&self, catalog: &str) -> Result<Vec<SchemaInfo>, RestError> {
        let r: SchemaList = self
            .get_json(
                "/api/2.1/unity-catalog/schemas",
                &[("catalog_name", catalog)],
            )
            .await?;
        Ok(r.schemas.unwrap_or_default())
    }

    pub async fn list_tables(
        &self,
        catalog: &str,
        schema: &str,
    ) -> Result<Vec<TableInfo>, RestError> {
        let r: TableList = self
            .get_json(
                "/api/2.1/unity-catalog/tables",
                &[("catalog_name", catalog), ("schema_name", schema)],
            )
            .await?;
        Ok(r.tables.unwrap_or_default())
    }

    pub async fn get_table(&self, full_name: &str) -> Result<TableInfo, RestError> {
        self.get_json(&format!("/api/2.1/unity-catalog/tables/{full_name}"), &[])
            .await
    }

    // ---- Warehouses (§8.2) ----

    pub async fn list_warehouses(&self) -> Result<Vec<Warehouse>, RestError> {
        let r: WarehouseList = self.get_json("/api/2.0/sql/warehouses", &[]).await?;
        Ok(r.warehouses.unwrap_or_default())
    }

    pub async fn get_warehouse(&self, id: &str) -> Result<Warehouse, RestError> {
        self.get_json(&format!("/api/2.0/sql/warehouses/{id}"), &[])
            .await
    }

    pub async fn start_warehouse(&self, id: &str) -> Result<(), RestError> {
        self.post_empty(&format!("/api/2.0/sql/warehouses/{id}/start"))
            .await
    }

    // ---- Statement Execution (§8.4/§8.5) — inline JSON for the read-path spike ----

    pub async fn submit_statement(
        &self,
        warehouse_id: &str,
        sql: &str,
        params: &[StatementParam],
    ) -> Result<StatementResponse, RestError> {
        let body = ExecuteStatementRequest {
            warehouse_id,
            statement: sql,
            parameters: params,
            format: "JSON_ARRAY",
            disposition: "INLINE",
            wait_timeout: "10s",
            on_wait_timeout: "CONTINUE",
        };
        self.post_json("/api/2.0/sql/statements", &body).await
    }

    pub async fn get_statement(&self, statement_id: &str) -> Result<StatementResponse, RestError> {
        self.get_json(&format!("/api/2.0/sql/statements/{statement_id}"), &[])
            .await
    }

    pub async fn cancel_statement(&self, statement_id: &str) -> Result<(), RestError> {
        self.post_empty(&format!("/api/2.0/sql/statements/{statement_id}/cancel"))
            .await
    }
}

/// Terminal-state classifier shared by the poller.
pub fn is_terminal(state: &str) -> bool {
    matches!(state, "SUCCEEDED" | "FAILED" | "CANCELED" | "CLOSED")
}

// ---- Wire types ----

#[derive(Debug, Deserialize)]
struct ApiError {
    #[serde(default, alias = "error_code")]
    #[allow(dead_code)]
    error_code: Option<String>,
    #[serde(default)]
    message: String,
}

#[derive(Debug, Deserialize)]
struct CatalogList {
    catalogs: Option<Vec<CatalogInfo>>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CatalogInfo {
    pub name: String,
}

#[derive(Debug, Deserialize)]
struct SchemaList {
    schemas: Option<Vec<SchemaInfo>>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SchemaInfo {
    pub name: String,
    #[serde(default)]
    pub catalog_name: String,
}

#[derive(Debug, Deserialize)]
struct TableList {
    tables: Option<Vec<TableInfo>>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TableInfo {
    pub name: String,
    #[serde(default)]
    pub full_name: String,
    #[serde(default)]
    pub table_type: String,
    #[serde(default)]
    pub columns: Vec<ColumnInfo>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ColumnInfo {
    pub name: String,
    #[serde(default)]
    pub type_text: String,
    #[serde(default)]
    pub nullable: bool,
}

#[derive(Debug, Deserialize)]
struct WarehouseList {
    warehouses: Option<Vec<Warehouse>>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Warehouse {
    pub id: String,
    #[serde(default)]
    pub name: String,
    /// STARTING | RUNNING | STOPPING | STOPPED | DELETING
    #[serde(default)]
    pub state: String,
}

#[derive(Debug, Serialize)]
struct ExecuteStatementRequest<'a> {
    warehouse_id: &'a str,
    statement: &'a str,
    parameters: &'a [StatementParam],
    format: &'a str,
    disposition: &'a str,
    wait_timeout: &'a str,
    on_wait_timeout: &'a str,
}

#[derive(Debug, Clone, Deserialize)]
pub struct StatementResponse {
    pub statement_id: Option<String>,
    pub status: StatementStatus,
    #[serde(default)]
    pub manifest: Option<Manifest>,
    #[serde(default)]
    pub result: Option<ResultData>,
}
#[derive(Debug, Clone, Deserialize)]
pub struct StatementStatus {
    pub state: String,
    #[serde(default)]
    pub error: Option<StatementError>,
}
#[derive(Debug, Clone, Deserialize)]
pub struct StatementError {
    #[serde(default)]
    pub message: String,
}
#[derive(Debug, Clone, Deserialize)]
pub struct Manifest {
    #[serde(default)]
    pub schema: SchemaManifest,
}
#[derive(Debug, Clone, Default, Deserialize)]
pub struct SchemaManifest {
    #[serde(default)]
    pub columns: Vec<ManifestColumn>,
}
#[derive(Debug, Clone, Deserialize)]
pub struct ManifestColumn {
    pub name: String,
}
#[derive(Debug, Clone, Deserialize)]
pub struct ResultData {
    #[serde(default)]
    pub data_array: Vec<Vec<Option<String>>>,
}

impl StatementResponse {
    /// Column names from the manifest (inline JSON results).
    pub fn column_names(&self) -> Vec<String> {
        self.manifest
            .as_ref()
            .map(|m| m.schema.columns.iter().map(|c| c.name.clone()).collect())
            .unwrap_or_default()
    }
    pub fn rows(&self) -> Vec<Vec<Option<String>>> {
        self.result
            .as_ref()
            .map(|r| r.data_array.clone())
            .unwrap_or_default()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn client(server: &mockito::Server) -> DatabricksClient {
        DatabricksClient::new(
            Url::parse(&server.url()).unwrap(),
            Secret::new("test-access-token"),
        )
    }

    #[tokio::test]
    async fn lists_catalogs_with_bearer() {
        let mut server = mockito::Server::new_async().await;
        let m = server
            .mock("GET", "/api/2.1/unity-catalog/catalogs")
            .match_header("authorization", "Bearer test-access-token")
            .with_status(200)
            .with_body(r#"{"catalogs":[{"name":"main"},{"name":"samples"}]}"#)
            .create_async()
            .await;
        let cats = client(&server).list_catalogs().await.unwrap();
        m.assert_async().await;
        assert_eq!(
            cats.iter().map(|c| c.name.as_str()).collect::<Vec<_>>(),
            ["main", "samples"]
        );
    }

    #[tokio::test]
    async fn api_error_maps_status_and_message() {
        let mut server = mockito::Server::new_async().await;
        server
            .mock("GET", "/api/2.0/sql/warehouses")
            .with_status(403)
            .with_body(r#"{"error_code":"PERMISSION_DENIED","message":"no access"}"#)
            .create_async()
            .await;
        let err = client(&server).list_warehouses().await.unwrap_err();
        match err {
            RestError::Api { status, message } => {
                assert_eq!(status, 403);
                assert_eq!(message, "no access");
            }
            other => panic!("unexpected {other:?}"),
        }
    }

    #[tokio::test]
    async fn start_warehouse_posts() {
        let mut server = mockito::Server::new_async().await;
        let m = server
            .mock("POST", "/api/2.0/sql/warehouses/wh-1/start")
            .with_status(200)
            .with_body("{}")
            .create_async()
            .await;
        client(&server).start_warehouse("wh-1").await.unwrap();
        m.assert_async().await;
    }

    #[tokio::test]
    async fn statement_inline_rows_parsed() {
        let mut server = mockito::Server::new_async().await;
        server
            .mock("POST", "/api/2.0/sql/statements")
            .with_status(200)
            .with_body(
                r#"{"statement_id":"01ef","status":{"state":"SUCCEEDED"},
                    "manifest":{"schema":{"columns":[{"name":"customer_id"},{"name":"company"}]}},
                    "result":{"data_array":[["83728","ACME"],["83729","Foo BV"]]}}"#,
            )
            .create_async()
            .await;
        let resp = client(&server)
            .submit_statement("wh-1", "SELECT 1", &[])
            .await
            .unwrap();
        assert_eq!(resp.status.state, "SUCCEEDED");
        assert!(is_terminal(&resp.status.state));
        assert_eq!(resp.column_names(), ["customer_id", "company"]);
        assert_eq!(resp.rows().len(), 2);
        assert_eq!(resp.rows()[0][1], Some("ACME".to_string()));
    }

    #[test]
    fn terminal_states() {
        assert!(is_terminal("SUCCEEDED"));
        assert!(is_terminal("FAILED"));
        assert!(!is_terminal("RUNNING"));
        assert!(!is_terminal("PENDING"));
    }
}
