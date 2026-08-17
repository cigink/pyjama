//! Authenticated session state + refresh policy (P1.2/P1.3).
//!
//! The access token lives in memory only (a [`Secret`]); refresh material is
//! persisted through [`crate::keystore`], never here. This module owns the pure
//! decision logic — "is the token close enough to expiry that we must refresh
//! before the next remote call?" — so it can be unit-tested without any clock or
//! network.

use std::time::{Duration, SystemTime};

use crate::logging::Secret;

/// Refresh proactively when this close to expiry, to avoid a call failing
/// mid-flight on a just-expired token.
pub const REFRESH_SKEW: Duration = Duration::from_secs(120);

/// In-memory session for the signed-in user.
pub struct AuthSession {
    pub workspace_url: String,
    pub user_subject: String,
    pub access_token: Secret,
    pub access_token_expires_at: SystemTime,
    /// Opaque reference to the refresh credential stored in the OS keystore.
    pub refresh_credential_ref: String,
    pub scopes: Vec<String>,
}

impl AuthSession {
    /// True when the token is expired or within [`REFRESH_SKEW`] of expiry.
    pub fn needs_refresh_at(&self, now: SystemTime) -> bool {
        match self.access_token_expires_at.duration_since(now) {
            Ok(remaining) => remaining <= REFRESH_SKEW,
            Err(_) => true, // already past expiry
        }
    }

    pub fn needs_refresh(&self) -> bool {
        self.needs_refresh_at(SystemTime::now())
    }

    /// Replace the access token after a successful refresh.
    pub fn update_access_token(&mut self, token: Secret, expires_at: SystemTime) {
        self.access_token = token;
        self.access_token_expires_at = expires_at;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn session(expires_at: SystemTime) -> AuthSession {
        AuthSession {
            workspace_url: "https://x".into(),
            user_subject: "u".into(),
            access_token: Secret::new("tok"),
            access_token_expires_at: expires_at,
            refresh_credential_ref: "ref".into(),
            scopes: vec![],
        }
    }

    #[test]
    fn fresh_token_no_refresh() {
        let now = SystemTime::now();
        let s = session(now + Duration::from_secs(3600));
        assert!(!s.needs_refresh_at(now));
    }

    #[test]
    fn near_expiry_triggers_refresh() {
        let now = SystemTime::now();
        let s = session(now + Duration::from_secs(60)); // within 120s skew
        assert!(s.needs_refresh_at(now));
    }

    #[test]
    fn expired_triggers_refresh() {
        let now = SystemTime::now();
        let s = session(now - Duration::from_secs(1));
        assert!(s.needs_refresh_at(now));
    }
}
