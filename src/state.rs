//! Registry of deployed services (`state.json`).

use std::collections::BTreeMap;
use std::path::PathBuf;

use anyhow::Context;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ServiceEntry {
    pub unit_name: String,
    pub project_dir: String,
    pub url: String,
    #[serde(default)]
    pub urls: Vec<String>,
}

#[derive(Debug, Default, Serialize, Deserialize)]
struct StateFile {
    #[serde(flatten)]
    services: BTreeMap<String, ServiceEntry>,
}

fn load() -> StateFile {
    let path = crate::paths::state_file();
    match std::fs::read(&path) {
        Ok(bytes) => serde_json::from_slice(&bytes).unwrap_or_default(),
        Err(_) => StateFile::default(),
    }
}

fn store(state: &StateFile) -> anyhow::Result<()> {
    let path = crate::paths::state_file();
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).with_context(|| format!("creating {}", dir.display()))?;
    }
    // Atomic write temp + rename: the live registry (project dirs + ports) is
    // 0600 from its first byte, a pre-planted symlink at the destination is
    // replaced instead of followed, and readers never see a partial file.
    crate::atomic::write_atomic(&path, &serde_json::to_vec_pretty(state)?)
        .with_context(|| format!("writing {}", path.display()))?;
    Ok(())
}

/// Register a deployed service.
pub fn register(name: &str, entry: ServiceEntry) -> anyhow::Result<()> {
    let mut st = load();
    st.services.insert(name.to_string(), entry);
    store(&st)
}

/// Remove a service from the registry (idempotent).
pub fn unregister(name: &str) -> anyhow::Result<()> {
    let mut st = load();
    st.services.remove(name);
    store(&st)
}

/// All registered services, sorted by name.
pub fn list() -> Vec<(String, ServiceEntry)> {
    load().services.into_iter().collect()
}

/// Look up a single service.
pub fn get(name: &str) -> Option<ServiceEntry> {
    load().services.get(name).cloned()
}

/// Path helper for tests / callers needing the clone dir of a service.
pub fn service_dir_hint(name: &str) -> Option<PathBuf> {
    get(name).map(|e| PathBuf::from(e.project_dir))
}
