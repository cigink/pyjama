//! PKCE (RFC 7636) for the OAuth U2M authorization-code flow (P1.1).
//!
//! We generate a high-entropy `code_verifier`, derive the S256 `code_challenge`,
//! and a `state` value for CSRF protection on the loopback redirect.

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use rand::RngCore;
use sha2::{Digest, Sha256};

use crate::logging::Secret;

/// A PKCE pair plus the CSRF `state`. `verifier` is a [`Secret`] — it must never
/// be logged; only the `challenge` and `state` travel in the authorize URL.
pub struct Pkce {
    pub verifier: Secret,
    pub challenge: String,
    pub state: String,
}

fn random_urlsafe(bytes: usize) -> String {
    let mut buf = vec![0u8; bytes];
    rand::thread_rng().fill_bytes(&mut buf);
    URL_SAFE_NO_PAD.encode(buf)
}

impl Pkce {
    pub fn generate() -> Pkce {
        // 32 random bytes -> 43-char verifier (within the 43..128 spec range).
        let verifier = random_urlsafe(32);
        let challenge = s256_challenge(&verifier);
        let state = random_urlsafe(16);
        Pkce {
            verifier: Secret::new(verifier),
            challenge,
            state,
        }
    }
}

/// Derive the S256 code challenge from a verifier.
pub fn s256_challenge(verifier: &str) -> String {
    let digest = Sha256::digest(verifier.as_bytes());
    URL_SAFE_NO_PAD.encode(digest)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rfc7636_reference_vector() {
        // From RFC 7636 Appendix B.
        let verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk";
        assert_eq!(
            s256_challenge(verifier),
            "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
        );
    }

    #[test]
    fn generate_is_unique_and_valid_length() {
        let a = Pkce::generate();
        let b = Pkce::generate();
        assert_ne!(a.verifier.expose(), b.verifier.expose());
        assert_ne!(a.state, b.state);
        assert!(a.verifier.expose().len() >= 43);
        // challenge must match its verifier
        assert_eq!(s256_challenge(a.verifier.expose()), a.challenge);
    }
}
