//! Auth orchestration (Phase 1 — ties Epic A together).
//!
//! Owns the live [`AuthSession`], runs the interactive U2M flow (loopback +
//! browser), refreshes proactively before remote calls, and stores refresh
//! material in the OS keystore. Commands go through this service to obtain a
//! valid access token; the token never leaves the process.

use std::io::{Read, Write};
use std::net::TcpListener;
use std::sync::{Arc, Mutex};

use url::Url;

use crate::config::DatabricksConfig;
use crate::keystore::KeyStore;
use crate::logging::{new_operation_id, Secret};
use crate::oauth;
use crate::pkce::Pkce;
use crate::session::AuthSession;

/// Single-user MVP: one refresh credential keyed by a stable name.
const REFRESH_KEY: &str = "databricks-refresh";

/// Fixed loopback port registered for the `databricks-cli` public OAuth client.
const REDIRECT_PORT: u16 = 8020;

pub struct AuthService {
    config: DatabricksConfig,
    keystore: Arc<dyn KeyStore>,
    http: reqwest::Client,
    session: Mutex<Option<AuthSession>>,
}

#[derive(Debug, thiserror::Error)]
pub enum AuthError {
    #[error("not configured: {0}")]
    Config(String),
    #[error("not authenticated")]
    NotAuthenticated,
    #[error("session expired; please sign in again")]
    Expired,
    #[error("oauth error: {0}")]
    OAuth(String),
    #[error("loopback error: {0}")]
    Loopback(String),
}

/// What a command needs to talk to Databricks: base URL + a fresh access token.
pub struct AccessContext {
    pub base: Url,
    pub token: Secret,
}

impl AuthService {
    pub fn new(config: DatabricksConfig, keystore: Arc<dyn KeyStore>) -> Self {
        AuthService {
            config,
            keystore,
            http: reqwest::Client::new(),
            session: Mutex::new(None),
        }
    }

    pub fn is_authenticated(&self) -> bool {
        self.session.lock().unwrap().is_some()
    }

    /// Non-secret config (workspace URL, public client id, warehouse id).
    pub fn config(&self) -> DatabricksConfig {
        self.config.clone()
    }

    /// Run the full interactive authorization-code + PKCE flow.
    pub async fn connect(&self) -> Result<crate::model::AuthUser, AuthError> {
        let base = self
            .config
            .base_url()
            .map_err(|e| AuthError::Config(e.to_string()))?;
        let operation_id = new_operation_id();
        tracing::info!(operation_id, "starting oauth u2m flow");

        let pkce = Pkce::generate();

        // The built-in `databricks-cli` public client registers exactly one
        // loopback redirect: http://localhost:8020 (the Databricks CLI's fixed
        // port, no path). Bind that exact port/URI or the authorize call is
        // rejected with "redirect_uri not registered". A custom OAuth app can
        // register its own URI; make this configurable then.
        let listener = TcpListener::bind(("127.0.0.1", REDIRECT_PORT)).map_err(|e| {
            AuthError::Loopback(format!("could not bind localhost:{REDIRECT_PORT} for the OAuth redirect ({e})"))
        })?;
        let redirect_uri = format!("http://localhost:{REDIRECT_PORT}");

        let authorize = oauth::build_authorize_url(
            &base,
            &self.config.client_id,
            &redirect_uri,
            &pkce.challenge,
            &pkce.state,
        );
        open_browser(authorize.as_str());

        // Block on a single redirect (runs on a blocking thread to keep the async
        // runtime free).
        let expected_state = pkce.state.clone();
        let redirect = tokio::task::spawn_blocking(move || capture_one_redirect(listener))
            .await
            .map_err(|e| AuthError::Loopback(e.to_string()))?
            .map_err(AuthError::Loopback)?;

        let code = oauth::extract_code(&redirect, &expected_state)
            .map_err(|e| AuthError::OAuth(e.to_string()))?;

        let tokens = oauth::exchange_code(
            &self.http,
            &base,
            &self.config.client_id,
            &code,
            &pkce.verifier,
            &redirect_uri,
        )
        .await
        .map_err(|e| AuthError::OAuth(e.to_string()))?;

        if let Some(refresh) = &tokens.refresh_token {
            self.keystore
                .set(REFRESH_KEY, refresh.expose())
                .map_err(|e| AuthError::OAuth(format!("keystore: {e}")))?;
        }

        let subject = "databricks-user".to_string();
        let scopes: Vec<String> = tokens
            .scope
            .clone()
            .map(|s| s.split(' ').map(String::from).collect())
            .unwrap_or_default();
        let user = crate::model::AuthUser {
            workspace_url: self.config.workspace_url.clone(),
            user_subject: subject.clone(),
            scopes: scopes.clone(),
        };

        *self.session.lock().unwrap() = Some(AuthSession {
            workspace_url: self.config.workspace_url.clone(),
            user_subject: subject,
            access_token: tokens.access_token,
            access_token_expires_at: tokens.expires_at,
            refresh_credential_ref: REFRESH_KEY.to_string(),
            scopes,
        });
        tracing::info!(operation_id, "oauth u2m flow complete");
        Ok(user)
    }

    /// Return a base URL + fresh access token, refreshing first if near expiry.
    /// Errors with [`AuthError::Expired`] when refresh is impossible — the caller
    /// should surface reauth (emit `auth://expired`).
    pub async fn access_context(&self) -> Result<AccessContext, AuthError> {
        let base = self
            .config
            .base_url()
            .map_err(|e| AuthError::Config(e.to_string()))?;

        let needs_refresh = {
            let guard = self.session.lock().unwrap();
            let session = guard.as_ref().ok_or(AuthError::NotAuthenticated)?;
            session.needs_refresh()
        };

        if needs_refresh {
            let refresh = self
                .keystore
                .get(REFRESH_KEY)
                .map_err(|_| AuthError::Expired)?;
            let tokens = oauth::refresh_tokens(
                &self.http,
                &base,
                &self.config.client_id,
                &Secret::new(refresh),
            )
            .await
            .map_err(|_| AuthError::Expired)?;
            if let Some(new_refresh) = &tokens.refresh_token {
                let _ = self.keystore.set(REFRESH_KEY, new_refresh.expose());
            }
            let mut guard = self.session.lock().unwrap();
            if let Some(session) = guard.as_mut() {
                session.update_access_token(tokens.access_token, tokens.expires_at);
            }
        }

        let guard = self.session.lock().unwrap();
        let session = guard.as_ref().ok_or(AuthError::NotAuthenticated)?;
        Ok(AccessContext {
            base,
            token: session.access_token.clone(),
        })
    }

    /// Sign out: drop the in-memory session and delete refresh material.
    pub fn logout(&self) {
        *self.session.lock().unwrap() = None;
        let _ = self.keystore.delete(REFRESH_KEY);
        tracing::info!("signed out; refresh credential removed");
    }
}

/// Accept a single loopback HTTP request and reconstruct the full redirect URL.
fn capture_one_redirect(listener: TcpListener) -> Result<Url, String> {
    let (mut stream, _addr) = listener.accept().map_err(|e| e.to_string())?;
    let mut buf = [0u8; 4096];
    let n = stream.read(&mut buf).map_err(|e| e.to_string())?;
    let request = String::from_utf8_lossy(&buf[..n]);
    // First line: "GET /callback?code=...&state=... HTTP/1.1"
    let path = request
        .lines()
        .next()
        .and_then(|l| l.split_whitespace().nth(1))
        .ok_or_else(|| "malformed request".to_string())?;

    let body = "<html><body style=\"font-family:sans-serif;text-align:center;padding-top:60px\">\
                <h2>PyJama</h2><p>Sign-in complete. You can close this tab.</p></body></html>";
    let response = format!(
        "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
        body.len(),
        body
    );
    let _ = stream.write_all(response.as_bytes());

    Url::parse(&format!("http://127.0.0.1{path}")).map_err(|e| e.to_string())
}

/// Open a URL in the user's default browser. Best-effort; the URL is also
/// available to the frontend if this fails.
fn open_browser(url: &str) {
    #[cfg(target_os = "macos")]
    let cmd = ("open", vec![url]);
    #[cfg(target_os = "windows")]
    let cmd = ("cmd", vec!["/C", "start", "", url]);
    #[cfg(all(unix, not(target_os = "macos")))]
    let cmd = ("xdg-open", vec![url]);

    let _ = std::process::Command::new(cmd.0).args(cmd.1).spawn();
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::keystore::MemoryKeyStore;

    fn service() -> AuthService {
        let config = DatabricksConfig {
            workspace_url: "https://dbc-abc.cloud.databricks.com".into(),
            client_id: "databricks-cli".into(),
            warehouse_id: None,
        };
        AuthService::new(config, Arc::new(MemoryKeyStore::default()))
    }

    #[tokio::test]
    async fn access_context_without_session_is_not_authenticated() {
        let svc = service();
        assert!(!svc.is_authenticated());
        assert!(matches!(
            svc.access_context().await,
            Err(AuthError::NotAuthenticated)
        ));
    }

    #[tokio::test]
    async fn logout_clears_refresh_credential() {
        let svc = service();
        svc.keystore.set(REFRESH_KEY, "rt").unwrap();
        svc.logout();
        assert!(svc.keystore.get(REFRESH_KEY).is_err());
    }
}
