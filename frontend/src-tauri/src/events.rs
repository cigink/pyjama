//! Event channel names emitted from the Rust core to the frontend
//! (IMPLEMENTATION_PLAN §17.1). Phase 0 defines the names; later phases emit
//! real payloads. Kept as constants so both sides agree on one source of truth.

pub const CHECKOUT_PROGRESS: &str = "checkout://progress";
pub const CHECKOUT_COMPLETED: &str = "checkout://completed";
pub const CHECKOUT_FAILED: &str = "checkout://failed";

pub const PIPELINE_INVALIDATED: &str = "pipeline://invalidated";
pub const PIPELINE_EXECUTION_PROGRESS: &str = "pipeline://execution-progress";

pub const WATCHER_FILE_DETECTED: &str = "watcher://file-detected";
pub const WATCHER_FILE_ERROR: &str = "watcher://file-error";

pub const COMMIT_PROGRESS: &str = "commit://progress";
pub const COMMIT_COMPLETED: &str = "commit://completed";
pub const COMMIT_FAILED: &str = "commit://failed";

pub const AUTH_EXPIRED: &str = "auth://expired";

/// All event names, exposed so the frontend bridge and tests can assert the set
/// stays in sync.
pub const ALL: &[&str] = &[
    CHECKOUT_PROGRESS,
    CHECKOUT_COMPLETED,
    CHECKOUT_FAILED,
    PIPELINE_INVALIDATED,
    PIPELINE_EXECUTION_PROGRESS,
    WATCHER_FILE_DETECTED,
    WATCHER_FILE_ERROR,
    COMMIT_PROGRESS,
    COMMIT_COMPLETED,
    COMMIT_FAILED,
    AUTH_EXPIRED,
];
