//! OS-backed secret storage (P1.2/P1.4).
//!
//! Refresh material and any wrapped keys are stored in the OS credential store
//! (macOS Keychain / Windows Credential Manager) via the `keyring` crate, keyed
//! by an opaque reference. Access tokens are *not* stored here — they stay in
//! process memory (see [`crate::session`]).
//!
//! The [`KeyStore`] trait lets tests use an in-memory fake and keeps the OS
//! dependency out of unit tests.

use std::collections::HashMap;
use std::sync::Mutex;

const SERVICE: &str = "com.pyjama.workspace";

#[derive(Debug, thiserror::Error)]
pub enum KeyStoreError {
    #[error("keystore entry not found")]
    NotFound,
    #[error("keystore backend error: {0}")]
    Backend(String),
}

pub trait KeyStore: Send + Sync {
    fn set(&self, key: &str, secret: &str) -> Result<(), KeyStoreError>;
    fn get(&self, key: &str) -> Result<String, KeyStoreError>;
    fn delete(&self, key: &str) -> Result<(), KeyStoreError>;
}

/// Production keystore backed by the OS credential manager.
pub struct OsKeyStore;

impl KeyStore for OsKeyStore {
    fn set(&self, key: &str, secret: &str) -> Result<(), KeyStoreError> {
        let entry =
            keyring::Entry::new(SERVICE, key).map_err(|e| KeyStoreError::Backend(e.to_string()))?;
        entry
            .set_password(secret)
            .map_err(|e| KeyStoreError::Backend(e.to_string()))
    }

    fn get(&self, key: &str) -> Result<String, KeyStoreError> {
        let entry =
            keyring::Entry::new(SERVICE, key).map_err(|e| KeyStoreError::Backend(e.to_string()))?;
        match entry.get_password() {
            Ok(v) => Ok(v),
            Err(keyring::Error::NoEntry) => Err(KeyStoreError::NotFound),
            Err(e) => Err(KeyStoreError::Backend(e.to_string())),
        }
    }

    fn delete(&self, key: &str) -> Result<(), KeyStoreError> {
        let entry =
            keyring::Entry::new(SERVICE, key).map_err(|e| KeyStoreError::Backend(e.to_string()))?;
        match entry.delete_password() {
            Ok(()) => Ok(()),
            Err(keyring::Error::NoEntry) => Ok(()), // idempotent
            Err(e) => Err(KeyStoreError::Backend(e.to_string())),
        }
    }
}

/// In-memory keystore for tests and headless CI.
#[derive(Default)]
pub struct MemoryKeyStore {
    map: Mutex<HashMap<String, String>>,
}

impl KeyStore for MemoryKeyStore {
    fn set(&self, key: &str, secret: &str) -> Result<(), KeyStoreError> {
        self.map
            .lock()
            .unwrap()
            .insert(key.to_string(), secret.to_string());
        Ok(())
    }

    fn get(&self, key: &str) -> Result<String, KeyStoreError> {
        self.map
            .lock()
            .unwrap()
            .get(key)
            .cloned()
            .ok_or(KeyStoreError::NotFound)
    }

    fn delete(&self, key: &str) -> Result<(), KeyStoreError> {
        self.map.lock().unwrap().remove(key);
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn memory_store_round_trip_and_delete() {
        let ks = MemoryKeyStore::default();
        assert!(matches!(
            ks.get("refresh:u").unwrap_err(),
            KeyStoreError::NotFound
        ));
        ks.set("refresh:u", "refresh-token-value").unwrap();
        assert_eq!(ks.get("refresh:u").unwrap(), "refresh-token-value");
        ks.delete("refresh:u").unwrap();
        assert!(matches!(
            ks.get("refresh:u").unwrap_err(),
            KeyStoreError::NotFound
        ));
        // delete is idempotent
        ks.delete("refresh:u").unwrap();
    }
}
