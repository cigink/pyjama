//! Structured logging + secret redaction (Phase 0 — Epic G, stories P0.6/P0.7).
//!
//! Two guarantees enforced here from day one, because retrofitting them later is
//! painful (see IMPLEMENTATION_PLAN §22.1, §19.4):
//!   1. Logs are structured JSON carrying an `operation_id` where relevant.
//!   2. Secrets — OAuth access/refresh tokens, presigned result URLs, SQL
//!      parameter values — never reach the log sink. The [`Secret`] newtype makes
//!      that the *default* by refusing to render its inner value.

use std::fmt;
use uuid::Uuid;

/// A value that must never be logged, serialized, or Debug-printed in the clear.
///
/// `Debug`/`Display` render `"***"`. Serialization emits `"***"` too, so a
/// `Secret` embedded in a struct that is accidentally logged as JSON still leaks
/// nothing. Read the real value only through [`Secret::expose`], which is
/// deliberately verbose to make review easy.
#[derive(Clone, PartialEq, Eq)]
pub struct Secret(String);

impl Secret {
    pub fn new(value: impl Into<String>) -> Self {
        Secret(value.into())
    }

    /// Access the underlying secret. Call sites should be rare and obvious.
    pub fn expose(&self) -> &str {
        &self.0
    }

    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }
}

impl fmt::Debug for Secret {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("Secret(***)")
    }
}

impl fmt::Display for Secret {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("***")
    }
}

impl serde::Serialize for Secret {
    fn serialize<S: serde::Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        s.serialize_str("***")
    }
}

impl From<String> for Secret {
    fn from(v: String) -> Self {
        Secret(v)
    }
}

/// Best-effort scrub of a free-form string before it is logged. Redacts anything
/// that looks like a bearer token or a presigned URL. This is a backstop; the
/// primary defense is never passing secrets to the logger at all.
pub fn scrub(input: &str) -> String {
    let mut out = String::with_capacity(input.len());
    for token in input.split_inclusive(char::is_whitespace) {
        let trimmed = token.trim_end();
        if looks_sensitive(trimmed) {
            out.push_str("***");
            out.push_str(&token[trimmed.len()..]); // preserve trailing whitespace
        } else {
            out.push_str(token);
        }
    }
    out
}

fn looks_sensitive(s: &str) -> bool {
    let lower = s.to_ascii_lowercase();
    lower.starts_with("bearer")
        || lower.starts_with("https://")
            && (lower.contains("x-amz-") || lower.contains("sig=") || lower.contains("token="))
        || (s.len() > 40
            && s.chars()
                .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-')))
}

/// Fresh operation identifier used to correlate the start/end of a logical
/// operation (checkout, commit, import) across log lines and the encrypted
/// operation journal (IMPLEMENTATION_PLAN §18.3).
pub fn new_operation_id() -> String {
    Uuid::new_v4().to_string()
}

/// Initialize the JSON tracing subscriber once at process start. Idempotent:
/// a second call is a no-op so tests can call it freely.
pub fn init() {
    use tracing_subscriber::{fmt, prelude::*, EnvFilter};

    let filter = EnvFilter::try_from_env("PYJAMA_LOG").unwrap_or_else(|_| EnvFilter::new("info"));
    let _ = tracing_subscriber::registry()
        .with(filter)
        .with(
            fmt::layer()
                .json()
                .with_current_span(true)
                .with_span_list(false),
        )
        .try_init();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn secret_never_renders_value() {
        let s = Secret::new("dapi-super-secret-token-value");
        assert_eq!(format!("{s}"), "***");
        assert_eq!(format!("{s:?}"), "Secret(***)");
        assert_eq!(serde_json::to_string(&s).unwrap(), "\"***\"");
        assert_eq!(s.expose(), "dapi-super-secret-token-value");
    }

    #[test]
    fn scrub_redacts_bearer_and_presigned() {
        let line = "GET https://store.example/chunk?sig=abc123 token eyJhbGciOiJIUzI1NiJ9.aVeryLongOpaqueAccessTokenValue1234567890";
        let out = scrub(line);
        assert!(
            !out.contains("eyJhbGci"),
            "long token should be redacted: {out}"
        );
        assert!(
            !out.contains("sig=abc123"),
            "presigned url should be redacted: {out}"
        );
        assert!(out.contains("***"));
    }
}
