//! Application-managed workspace filesystem (Phase 0 — Epic F, P0.4/P0.5).
//!
//! Implements the on-disk layout from IMPLEMENTATION_PLAN §9.1 and a serde
//! round-trippable manifest (§18.1). Enterprise data must never land in
//! Downloads/Documents (PRD §21), so everything lives under the OS app-data dir
//! with a restrictive directory mode.
//!
//! NOTE: `manifest.enc` / `operation-journal.enc` keep the `.enc` suffix as the
//! stable contract, but Phase 0 writes them as plaintext JSON. Encryption
//! (the WDEK key hierarchy) lands in Phase 2; only this module changes then.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use chrono::Utc;
use serde::{Deserialize, Serialize};
use thiserror::Error;
use uuid::Uuid;

#[derive(Debug, Error)]
pub enum WorkspaceError {
    #[error("could not resolve OS application-data directory")]
    NoDataDir,
    #[error("workspace {0} not found")]
    NotFound(String),
    #[error("io error: {0}")]
    Io(#[from] io::Error),
    #[error("manifest (de)serialization error: {0}")]
    Serde(#[from] serde_json::Error),
}

pub type Result<T> = std::result::Result<T, WorkspaceError>;

/// Persisted workspace manifest — the source of truth for reopening a workspace
/// across restarts. Mirrors IMPLEMENTATION_PLAN §18.1.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Manifest {
    pub workspace_id: String,
    pub name: String,
    pub created_at: String,
    pub source: SourceRef,
    pub storage: Storage,
    pub pipeline_revision: u64,
    pub pipeline: serde_json::Value,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct SourceRef {
    pub workspace_url: String,
    pub table: String,
    pub base_version: u64,
    pub columns: Vec<String>,
    pub row_key: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct Storage {
    pub partition_files: u32,
    pub row_count: u64,
    pub logical_bytes: u64,
    pub encryption_key_id: Option<String>,
}

/// Root: `<app-data>/PyJama/workspaces`.
pub fn workspaces_root() -> Result<PathBuf> {
    let base = dirs::data_dir().ok_or(WorkspaceError::NoDataDir)?;
    Ok(base.join("PyJama").join("workspaces"))
}

fn workspace_dir(id: &str) -> Result<PathBuf> {
    Ok(workspaces_root()?.join(id))
}

/// Create a new workspace directory tree and write an initial manifest.
/// Returns the new workspace id.
pub fn create(name: &str) -> Result<Manifest> {
    let id = Uuid::new_v4().to_string();
    let dir = workspace_dir(&id)?;
    for sub in ["", "data", "local_sources", "changes", "cache"] {
        fs::create_dir_all(dir.join(sub))?;
    }
    restrict_permissions(&dir)?;

    let manifest = Manifest {
        workspace_id: id.clone(),
        name: name.to_string(),
        created_at: Utc::now().to_rfc3339(),
        source: SourceRef::default(),
        storage: Storage::default(),
        pipeline_revision: 0,
        pipeline: serde_json::json!([]),
    };
    write_manifest(&manifest)?;
    // Touch the encrypted-operation-journal placeholder so the layout is complete.
    fs::write(dir.join("operation-journal.enc"), b"[]")?;
    Ok(manifest)
}

/// Persist a manifest to `<workspace>/manifest.enc` (plaintext JSON in Phase 0).
pub fn write_manifest(manifest: &Manifest) -> Result<()> {
    let dir = workspace_dir(&manifest.workspace_id)?;
    fs::create_dir_all(&dir)?;
    let bytes = serde_json::to_vec_pretty(manifest)?;
    fs::write(dir.join("manifest.enc"), bytes)?;
    Ok(())
}

/// Read a manifest back by workspace id.
pub fn read_manifest(id: &str) -> Result<Manifest> {
    let path = workspace_dir(id)?.join("manifest.enc");
    if !path.exists() {
        return Err(WorkspaceError::NotFound(id.to_string()));
    }
    let bytes = fs::read(path)?;
    Ok(serde_json::from_slice(&bytes)?)
}

/// Enumerate existing workspace ids (directories under the root).
pub fn list() -> Result<Vec<String>> {
    let root = workspaces_root()?;
    if !root.exists() {
        return Ok(vec![]);
    }
    let mut out = vec![];
    for entry in fs::read_dir(root)? {
        let entry = entry?;
        if entry.file_type()?.is_dir() {
            if let Some(name) = entry.file_name().to_str() {
                if entry.path().join("manifest.enc").exists() {
                    out.push(name.to_string());
                }
            }
        }
    }
    out.sort();
    Ok(out)
}

#[cfg(unix)]
fn restrict_permissions(dir: &Path) -> Result<()> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(dir, fs::Permissions::from_mode(0o700))?;
    Ok(())
}

#[cfg(not(unix))]
fn restrict_permissions(_dir: &Path) -> Result<()> {
    // Windows ACL hardening is handled in Phase 8; the app-data dir is already
    // per-user on Windows.
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn manifest_round_trips() {
        let m = create("Round Trip Test").expect("create");
        let read = read_manifest(&m.workspace_id).expect("read");
        assert_eq!(read.workspace_id, m.workspace_id);
        assert_eq!(read.name, "Round Trip Test");
        assert_eq!(read.pipeline_revision, 0);

        // layout exists
        let dir = workspace_dir(&m.workspace_id).unwrap();
        for sub in ["data", "local_sources", "changes", "cache"] {
            assert!(dir.join(sub).is_dir(), "missing {sub}");
        }
        assert!(dir.join("operation-journal.enc").exists());

        assert!(list().unwrap().contains(&m.workspace_id));

        // cleanup
        let _ = fs::remove_dir_all(dir);
    }
}
