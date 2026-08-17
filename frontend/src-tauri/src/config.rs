//! Non-secret runtime configuration (Phase 1).
//!
//! Everything here is safe to log and to keep in a plaintext file: the workspace
//! host, the OAuth *public* client id, and the selected warehouse. Secrets
//! (tokens, refresh material) never live here — they go through [`crate::keystore`].
//!
//! Resolution order: environment variables (`PYJAMA_*`) override a
//! `pyjama.toml`-style file. For Phase 1 we read env only; the file loader is a
//! thin future hook.

use serde::{Deserialize, Serialize};
use url::Url;

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct DatabricksConfig {
    /// e.g. https://dbc-abc123.cloud.databricks.com
    pub workspace_url: String,
    /// Public OAuth client id for the U2M app. The workspace's built-in default
    /// public client id is `databricks-cli`.
    pub client_id: String,
    /// Selected SQL warehouse id (may be empty until the user picks one).
    pub warehouse_id: Option<String>,
}

impl DatabricksConfig {
    /// Load from `PYJAMA_WORKSPACE_URL`, `PYJAMA_CLIENT_ID`, `PYJAMA_WAREHOUSE_ID`.
    /// `client_id` defaults to the Databricks CLI public client when unset.
    pub fn from_env() -> Self {
        DatabricksConfig {
            workspace_url: std::env::var("PYJAMA_WORKSPACE_URL").unwrap_or_default(),
            client_id: std::env::var("PYJAMA_CLIENT_ID")
                .unwrap_or_else(|_| "databricks-cli".to_string()),
            warehouse_id: std::env::var("PYJAMA_WAREHOUSE_ID")
                .ok()
                .filter(|s| !s.is_empty()),
        }
    }

    /// Validate and return the normalized workspace base URL (scheme + host, no
    /// trailing slash). Errors if unset or not https.
    pub fn base_url(&self) -> Result<Url, ConfigError> {
        if self.workspace_url.is_empty() {
            return Err(ConfigError::MissingWorkspaceUrl);
        }
        let url = Url::parse(&self.workspace_url).map_err(|_| ConfigError::InvalidWorkspaceUrl)?;
        if url.scheme() != "https" {
            return Err(ConfigError::InsecureWorkspaceUrl);
        }
        if url.host_str().is_none() {
            return Err(ConfigError::InvalidWorkspaceUrl);
        }
        Ok(url)
    }

    pub fn is_configured(&self) -> bool {
        self.base_url().is_ok() && !self.client_id.is_empty()
    }
}

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum ConfigError {
    #[error("workspace URL is not configured (set PYJAMA_WORKSPACE_URL)")]
    MissingWorkspaceUrl,
    #[error("workspace URL is not a valid URL")]
    InvalidWorkspaceUrl,
    #[error("workspace URL must use https")]
    InsecureWorkspaceUrl,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn valid_https_url_ok() {
        let c = DatabricksConfig {
            workspace_url: "https://dbc-abc.cloud.databricks.com".into(),
            client_id: "databricks-cli".into(),
            warehouse_id: None,
        };
        assert!(c.is_configured());
        assert_eq!(
            c.base_url().unwrap().host_str(),
            Some("dbc-abc.cloud.databricks.com")
        );
    }

    #[test]
    fn rejects_http_and_empty() {
        let mut c = DatabricksConfig::default();
        assert_eq!(c.base_url().unwrap_err(), ConfigError::MissingWorkspaceUrl);
        c.workspace_url = "http://insecure.example".into();
        assert_eq!(c.base_url().unwrap_err(), ConfigError::InsecureWorkspaceUrl);
        c.workspace_url = "not a url".into();
        assert_eq!(c.base_url().unwrap_err(), ConfigError::InvalidWorkspaceUrl);
    }
}
