//! Pre-fetch build dependencies BEFORE the sandboxed build.
//!
//! Build steps run with `PrivateNetwork=yes` (see `sandbox.rs`) so a hostile
//! `setup.py` / `postinstall` / `build.rs` fetched from a package registry can
//! never phone home. That is only safe if the build itself is fully offline —
//! every dependency it needs must already be on local disk. This module does
//! that pre-seeding during the pre-flight *host* phase, where a dependency
//! downloader (`pip`, and later `bun`/`pnpm`) may reach its registry.
//!
//! The security invariant is preserved: the downloader only *fetches blobs*;
//! it does not run the project's, nor the fetched packages', build code here.
//! The `--only-binary=:all:` flag is what makes that true for pip (wheels are
//! inert archives — no sdist is built, so no `setup.py` executes). Everything
//! fetched is executed later, and only inside the offline sandbox. This keeps
//! the data flow documented in `netlog.rs` honest: registry bytes cross the
//! network via the downloader tool (which already did so from inside the
//! sandbox before), not via this binary's allowlisted client.

use std::collections::BTreeMap;
use std::path::Path;
use std::process::Command;

/// Wheelhouse directory for a recipe's pip dependencies, under the project's
/// persistent cache (kept in sync with the pip cache env in `sandbox.rs`).
fn wheelhouse(project_dir: &Path) -> std::path::PathBuf {
    project_dir.join(".ghost-cache").join("pip-wheelhouse")
}

/// The cache-env redirects for `bun`/`pnpm` live in `sandbox.rs::cache_env`;
/// the host prefetch writes where those point (the recipe's `--store-dir`
/// placeholder for pnpm, and bun's cache dir for `bun install`), so the
/// built artifacts are found by the offline build step.
///
/// Environment of the host prefetch runner: current env, scrubbed of
/// credentials exactly like the sandbox scrubs them (a prefetch downloader
/// must never inherit a deployment token either).
fn host_env() -> BTreeMap<String, String> {
    let mut env: BTreeMap<String, String> = std::env::vars().collect();
    // Same credential-denylist as the build sandbox (sandbox.rs::scrub_env).
    for k in crate::hoster::sandbox::sandbox_scrub_denylist() {
        env.remove(*k);
    }
    env.insert("CI".to_string(), "1".to_string());
    env
}

/// Run one host-phase dependency prefetch command. The command is a
/// *downloader* (e.g. `bun install`, `pnpm fetch`, `pip download`) that may
/// reach its registry — but by design does not run fetched package code on
/// the host (bun and pnpm skip untrusted lifecycle scripts; pip
/// `--only-binary=:all:` wheels are inert archives). Project build code never
/// executes here; it runs later, inside the offline sandbox.
pub fn run_host_step(cmd: &str, project_dir: &Path, log: &dyn Fn(&str)) -> anyhow::Result<()> {
    crate::hoster::validate::validate_build_cmd(cmd).map_err(|r| anyhow::anyhow!("{r}: {cmd}"))?;
    let env = host_env();

    log(&format!("prefetch (host): {cmd}"));
    let status = Command::new("/bin/sh")
        .args(["-c", cmd])
        .current_dir(project_dir)
        .env_clear()
        .envs(&env)
        .stdout(std::process::Stdio::inherit())
        .stderr(std::process::Stdio::inherit())
        .status();
    match status? {
        s if s.success() => Ok(()),
        s => anyhow::bail!("prefetch step failed (exit {s}): {cmd}"),
    }
}

/// Marker written only after a full, successful `pip download`. Its presence
/// means the wheelhouse is a valid, complete offline source: a partial or
/// interrupted download is never trusted, so a killed run re-fetches.
const DONE: &str = ".done";

/// Return the env overrides that make the in-sandbox `pip install` consume
/// *only* the wheelhouse (no index, offline), when the wheelhouse has been
/// pre-seeded by the recipe's prefetch step. Empty vec = nothing ready.
///
/// The actual download happens in `prefetch_steps` (`pip download ...`), which
/// runs on the host. The marker file written by that successful download is
/// what this function keys on, so a partial wheelhouse is never trusted.
pub fn pip_offline_env(project_dir: &Path) -> Vec<(String, String)> {
    let req = project_dir.join("requirements.txt");
    if !req.is_file() {
        return Vec::new();
    }
    let wh = wheelhouse(project_dir);
    if wh.join(DONE).is_file() {
        offline_env(&wh)
    } else {
        Vec::new()
    }
}

/// Env overrides that force `pip install` to use only the local wheelhouse.
fn offline_env(wh: &Path) -> Vec<(String, String)> {
    let p = wh.to_string_lossy().into_owned();
    vec![
        ("PIP_NO_INDEX".to_string(), "1".to_string()),
        ("PIP_FIND_LINKS".to_string(), p),
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn no_requirements_means_no_work() {
        let dir = std::env::temp_dir().join(format!("gp-prefetch-none-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let env = pip_offline_env(&dir);
        assert!(env.is_empty());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn offline_env_is_index_free_and_local() {
        let wh = std::path::PathBuf::from("/x/.ghost-cache/pip-wheelhouse");
        let env = offline_env(&wh);
        assert_eq!(env[0], ("PIP_NO_INDEX".into(), "1".into()));
        assert!(env[1] == ("PIP_FIND_LINKS".into(), "/x/.ghost-cache/pip-wheelhouse".into()));
    }

    #[test]
    fn wheelhouse_env_only_when_marker_present() {
        let dir = std::env::temp_dir().join(format!("gp-prefetch-wh-{}", std::process::id()));
        let wh = dir.join(".ghost-cache").join("pip-wheelhouse");
        std::fs::create_dir_all(&wh).unwrap();
        std::fs::write(
            dir.join("requirements.txt"),
            "certifi==2026.7.22\n",
        )
        .unwrap();

        // No marker → no offline env (partial wheelhouse must never be used).
        assert!(pip_offline_env(&dir).is_empty());

        std::fs::write(wh.join(DONE), "ok").unwrap();
        let env = pip_offline_env(&dir);
        assert_eq!(env.len(), 2, "marker present → offline env returned: {env:?}");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn host_prefetch_step_runs_and_scrubs_credentials() {
        unsafe {
            std::env::set_var("GITHUB_TOKEN", "should-not-leak");
            std::env::set_var("CI", "0");
        }
        // The host prefetch environment must never carry a deployment
        // credential, and must force CI=1 (pip/pnpm refuse host runs that look
        // interactive, e.g. pnpm's module-dir purge prompt).
        let env = host_env();
        assert_eq!(env.get("GITHUB_TOKEN"), None, "credential must be scrubbed");
        assert_eq!(env.get("CI").map(String::as_str), Some("1"));
        unsafe {
            std::env::remove_var("GITHUB_TOKEN");
            std::env::set_var("CI", "0");
        }
    }

    #[test]
    fn host_prefetch_step_rejects_dangerous_command() {
        let dir = std::env::temp_dir().join(format!("gp-prefetch-bad-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let err = run_host_step("rm -rf /", &dir, &|_| {}).unwrap_err();
        assert!(err.to_string().contains("rejected"), "{err}");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
