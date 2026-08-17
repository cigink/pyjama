//! OAuth 2.0 user-to-machine (U2M) authorization-code + PKCE flow (P1.1/P1.3).
//!
//! Databricks OIDC endpoints live at a fixed path under the workspace host:
//!   - authorize: `{host}/oidc/v1/authorize`
//!   - token:     `{host}/oidc/v1/token`
//!
//! The interactive flow: generate PKCE, open the authorize URL in the user's
//! browser, capture the redirect on a loopback listener, then exchange the code
//! for tokens at the token endpoint. Refresh uses the `offline_access` scope.
//!
//! Token endpoint calls ([`exchange_code`], [`refresh_tokens`]) take a base URL
//! so they can be pointed at a mock server in tests.

use std::time::{Duration, SystemTime};

use serde::Deserialize;
use url::Url;

use crate::logging::Secret;

/// Scopes requested for U2M. `offline_access` yields a refresh token; `all-apis`
/// grants access to the REST surface the app uses.
pub const SCOPES: &str = "all-apis offline_access";

pub fn authorize_endpoint(base: &Url) -> Url {
    base.join("/oidc/v1/authorize").expect("static path")
}

pub fn token_endpoint(base: &Url) -> Url {
    base.join("/oidc/v1/token").expect("static path")
}

/// Build the browser authorize URL for the PKCE code flow.
pub fn build_authorize_url(
    base: &Url,
    client_id: &str,
    redirect_uri: &str,
    challenge: &str,
    state: &str,
) -> Url {
    let mut url = authorize_endpoint(base);
    url.query_pairs_mut()
        .append_pair("client_id", client_id)
        .append_pair("response_type", "code")
        .append_pair("redirect_uri", redirect_uri)
        .append_pair("scope", SCOPES)
        .append_pair("state", state)
        .append_pair("code_challenge", challenge)
        .append_pair("code_challenge_method", "S256");
    url
}

/// Tokens returned by the token endpoint. Secrets are wrapped so an accidental
/// log leaks nothing.
#[derive(Debug)]
pub struct Tokens {
    pub access_token: Secret,
    pub refresh_token: Option<Secret>,
    pub expires_at: SystemTime,
    pub scope: Option<String>,
}

#[derive(Debug, Deserialize)]
struct TokenResponse {
    access_token: String,
    #[serde(default)]
    refresh_token: Option<String>,
    #[serde(default = "default_expiry")]
    expires_in: u64,
    #[serde(default)]
    scope: Option<String>,
}

fn default_expiry() -> u64 {
    3600
}

impl TokenResponse {
    fn into_tokens(self) -> Tokens {
        Tokens {
            access_token: Secret::new(self.access_token),
            refresh_token: self.refresh_token.map(Secret::new),
            expires_at: SystemTime::now() + Duration::from_secs(self.expires_in),
            scope: self.scope,
        }
    }
}

#[derive(Debug, thiserror::Error)]
pub enum OAuthError {
    #[error("http error: {0}")]
    Http(String),
    #[error("token endpoint returned {status}: {body}")]
    TokenEndpoint { status: u16, body: String },
    #[error("state mismatch on redirect (possible CSRF)")]
    StateMismatch,
    #[error("authorization redirect missing code")]
    MissingCode,
}

/// Exchange an authorization code for tokens (PKCE verifier proves possession).
pub async fn exchange_code(
    client: &reqwest::Client,
    base: &Url,
    client_id: &str,
    code: &str,
    verifier: &Secret,
    redirect_uri: &str,
) -> Result<Tokens, OAuthError> {
    let params = [
        ("grant_type", "authorization_code"),
        ("code", code),
        ("client_id", client_id),
        ("redirect_uri", redirect_uri),
        ("code_verifier", verifier.expose()),
    ];
    post_token(client, base, &params).await
}

/// Exchange a refresh token for a fresh access token.
pub async fn refresh_tokens(
    client: &reqwest::Client,
    base: &Url,
    client_id: &str,
    refresh_token: &Secret,
) -> Result<Tokens, OAuthError> {
    let params = [
        ("grant_type", "refresh_token"),
        ("refresh_token", refresh_token.expose()),
        ("client_id", client_id),
        ("scope", SCOPES),
    ];
    post_token(client, base, &params).await
}

async fn post_token(
    client: &reqwest::Client,
    base: &Url,
    params: &[(&str, &str)],
) -> Result<Tokens, OAuthError> {
    let resp = client
        .post(token_endpoint(base))
        .form(params)
        .send()
        .await
        .map_err(|e| OAuthError::Http(e.to_string()))?;
    let status = resp.status();
    let body = resp
        .text()
        .await
        .map_err(|e| OAuthError::Http(e.to_string()))?;
    if !status.is_success() {
        // NB: never include the request form (it holds the refresh token) in errors.
        return Err(OAuthError::TokenEndpoint {
            status: status.as_u16(),
            body,
        });
    }
    let parsed: TokenResponse =
        serde_json::from_str(&body).map_err(|e| OAuthError::Http(e.to_string()))?;
    Ok(parsed.into_tokens())
}

/// Validate a loopback redirect URL and extract the authorization code,
/// checking `state` for CSRF.
pub fn extract_code(redirect: &Url, expected_state: &str) -> Result<String, OAuthError> {
    let mut code = None;
    let mut state = None;
    for (k, v) in redirect.query_pairs() {
        match k.as_ref() {
            "code" => code = Some(v.into_owned()),
            "state" => state = Some(v.into_owned()),
            _ => {}
        }
    }
    if state.as_deref() != Some(expected_state) {
        return Err(OAuthError::StateMismatch);
    }
    code.ok_or(OAuthError::MissingCode)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn base() -> Url {
        Url::parse("https://dbc-abc.cloud.databricks.com").unwrap()
    }

    #[test]
    fn authorize_url_has_pkce_and_scopes() {
        let u = build_authorize_url(
            &base(),
            "databricks-cli",
            "http://127.0.0.1:8020/callback",
            "CHAL",
            "STATE",
        );
        let q: std::collections::HashMap<_, _> = u.query_pairs().into_owned().collect();
        assert_eq!(u.path(), "/oidc/v1/authorize");
        assert_eq!(q["response_type"], "code");
        assert_eq!(q["code_challenge"], "CHAL");
        assert_eq!(q["code_challenge_method"], "S256");
        assert_eq!(q["state"], "STATE");
        assert!(q["scope"].contains("offline_access"));
    }

    #[test]
    fn extract_code_checks_state() {
        let ok = Url::parse("http://127.0.0.1:8020/callback?code=abc&state=xyz").unwrap();
        assert_eq!(extract_code(&ok, "xyz").unwrap(), "abc");

        let bad = Url::parse("http://127.0.0.1:8020/callback?code=abc&state=WRONG").unwrap();
        assert!(matches!(
            extract_code(&bad, "xyz"),
            Err(OAuthError::StateMismatch)
        ));

        let nocode = Url::parse("http://127.0.0.1:8020/callback?state=xyz").unwrap();
        assert!(matches!(
            extract_code(&nocode, "xyz"),
            Err(OAuthError::MissingCode)
        ));
    }

    #[tokio::test]
    async fn exchange_code_parses_tokens() {
        let mut server = mockito::Server::new_async().await;
        let m = server
            .mock("POST", "/oidc/v1/token")
            .match_body(mockito::Matcher::Regex("grant_type=authorization_code".into()))
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{"access_token":"AT","refresh_token":"RT","expires_in":3600,"scope":"all-apis offline_access"}"#)
            .create_async()
            .await;

        let base = Url::parse(&server.url()).unwrap();
        let client = reqwest::Client::new();
        let tokens = exchange_code(
            &client,
            &base,
            "databricks-cli",
            "the-code",
            &Secret::new("verifier"),
            "http://127.0.0.1:8020/callback",
        )
        .await
        .unwrap();
        m.assert_async().await;
        assert_eq!(tokens.access_token.expose(), "AT");
        assert_eq!(tokens.refresh_token.unwrap().expose(), "RT");
        assert!(tokens.expires_at > SystemTime::now());
    }

    #[tokio::test]
    async fn refresh_failure_surfaces_status_without_leaking() {
        let mut server = mockito::Server::new_async().await;
        server
            .mock("POST", "/oidc/v1/token")
            .with_status(401)
            .with_body("invalid_grant")
            .create_async()
            .await;

        let base = Url::parse(&server.url()).unwrap();
        let client = reqwest::Client::new();
        let err = refresh_tokens(
            &client,
            &base,
            "databricks-cli",
            &Secret::new("super-secret-refresh"),
        )
        .await
        .unwrap_err();
        match err {
            OAuthError::TokenEndpoint { status, body } => {
                assert_eq!(status, 401);
                assert!(!body.contains("super-secret-refresh"));
            }
            other => panic!("unexpected: {other:?}"),
        }
    }
}
