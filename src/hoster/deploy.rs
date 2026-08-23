//! Deploy sequence for the three curated demo services.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::Command;

use anyhow::Context;

use super::gitclone;
use super::models::{HostResult, RepoAnalysis};
use super::port::find_free_port;
use super::recipes::DemoRecipe;
use super::sandbox::run_build_cmd;
use super::secrets::write_env_file;
use super::units::{
    StartOutcome, UnitSpec, create_unit, remove_unit, service_logs, wait_until_active,
};

fn safe_dirname(name: &str) -> String {
    name.chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || matches!(c, '_' | '-') {
                c
            } else {
                '_'
            }
        })
        .collect()
}

/// Clone (or reuse) the repository into the permanent services directory.
pub fn clone_repo(analysis: &RepoAnalysis, work_dir: Option<&Path>) -> Option<PathBuf> {
    let base = work_dir
        .map(|p| p.to_path_buf())
        .unwrap_or_else(crate::paths::services_dir);
    std::fs::create_dir_all(&base).ok()?;

    let dir = base.join(safe_dirname(&analysis.name));
    if !dir.join(".git").is_dir() {
        if dir.is_dir() {
            let _ = std::fs::remove_dir_all(&dir);
        }
        let url = format!(
            "https://github.com/{}/{}.git",
            analysis.owner, analysis.name
        );
        let status = gitclone::clone(&url, &dir);
        eprintln!("clone: {}", status.last_message);
        if !status.ok {
            return None;
        }
    }
    Some(dir)
}

/// Fill recipe start command placeholders with concrete paths.
pub fn resolve_start(recipe: &DemoRecipe, project_dir: &Path, port: u16) -> String {
    let self_exe = std::env::current_exe()
        .map(|p| p.to_string_lossy().into_owned())
        .unwrap_or_else(|_| "demo-ghostprovider".into());

    let mut cmd = recipe.start_cmd.to_string();
    if recipe.language == "Go" {
        cmd = cmd.replace("{bin}", &project_dir.join("ghost-server").to_string_lossy());
    }
    cmd = cmd
        .replace(
            "{venv}",
            &project_dir.join(".venv/bin/python").to_string_lossy(),
        )
        .replace("{python}", "python3")
        .replace("{self}", &self_exe)
        .replace("{project}", &project_dir.to_string_lossy())
        .replace("{port}", &port.to_string());
    cmd
}

/// Patch SearXNG settings.yml: real secret key, loopback bind, chosen port.
fn prepare_searxng_config(project_dir: &Path, port: u16) {
    let settings = project_dir.join("searx/settings.yml");
    let Ok(content) = std::fs::read_to_string(&settings) else {
        return;
    };

    let secret_key = random_hex(32);
    let patched: Vec<String> = content
        .lines()
        .map(|line| {
            let indent_len = line.len() - line.trim_start().len();
            let indent = &line[..indent_len];
            let trimmed = line.trim_start();
            if trimmed.starts_with("secret_key:") {
                format!("{indent}secret_key: \"{secret_key}\"")
            } else if trimmed.starts_with("bind_address:") {
                format!("{indent}bind_address: \"127.0.0.1\"")
            } else if trimmed.starts_with("http_address:") && trimmed.contains(':') {
                // Some SearXNG versions use uwsgi-style http_address.
                format!("{indent}http_address: \"127.0.0.1:{port}\"")
            } else if trimmed.starts_with("port:") {
                format!("{indent}port: {port}")
            } else {
                line.to_string()
            }
        })
        .collect();

    let _ = std::fs::write(settings, patched.join("\n") + "\n");
}

fn random_hex(bytes: usize) -> String {
    // No rand crate: read the kernel CSPRNG; fall back to a time-derived
    // value only if /dev/urandom is somehow unavailable.
    let mut buf = vec![0u8; bytes];
    let ok = std::fs::File::open("/dev/urandom")
        .and_then(|mut f| {
            use std::io::Read;
            f.read_exact(&mut buf)?;
            Ok(())
        })
        .is_ok();
    if !ok {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        for (i, b) in buf.iter_mut().enumerate() {
            *b = ((nanos >> (i % 16)) as u8) ^ (std::process::id() as u8);
        }
    }
    buf.iter().map(|b| format!("{b:02x}")).collect()
}

/// Stop and delete a previously deployed unit so it can be replaced cleanly.
fn stop_existing(service_name: &str) {
    let unit = crate::paths::user_unit_dir().join(format!("{}.service", service_name));
    if unit.is_file() {
        remove_unit(service_name);
        super::secrets::remove_env_file(service_name);
    }
}

#[derive(Default)]
pub struct DeployHooks<'a> {
    pub on_status: Option<&'a dyn Fn(&str)>,
}

/// Outcome of a full pipeline run (parse → recipe → preflight → deploy).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DeployOutcome {
    Deployed,
    Rejected(&'static str),
    Failed,
}

/// Shared entry point used by both the TUI and the `__deploy` subcommand:
/// validate the URL against the curated recipes, preflight the build tools,
/// then run the full deploy pipeline. Progress goes through `log`.
pub fn run_deployment(url: &str, log: &dyn Fn(String)) -> DeployOutcome {
    let Some((owner, name)) = super::github::parse_github_url(url) else {
        log("! invalid GitHub URL format".into());
        return DeployOutcome::Rejected("bad-url");
    };
    let Some(recipe) = super::recipes::find_recipe(&owner, &name) else {
        log("! this demo only supports three services:".into());
        log("  VERT-sh/VERT · searxng/searxng · usememos/memos".into());
        return DeployOutcome::Rejected("not-curated");
    };

    // Preflight including per-recipe build tools (audit lesson).
    log("pre-flight checks...".into());
    let issues = super::preflight::preflight_check(recipe.tools);
    if !issues.is_empty() {
        for i in issues {
            log(format!("! {i}"));
        }
        log("! pre-flight failed, aborting".into());
        return DeployOutcome::Rejected("preflight");
    }

    let analysis = RepoAnalysis {
        url: url.to_string(),
        owner,
        name,
        language: recipe.language.into(),
        exists: true,
        clone_path: None,
        errors: vec![],
    };

    let result = deploy_service(
        &analysis,
        recipe,
        None,
        DeployHooks {
            on_status: Some(&|line| log(line.to_string())),
        },
    );

    for u in &result.urls {
        log(format!("listening on {u}"));
    }
    if !result.service_names.is_empty() && result.errors.is_empty() {
        DeployOutcome::Deployed
    } else {
        DeployOutcome::Failed
    }
}

/// Build, install, and start one curated demo service.
pub fn deploy_service(
    analysis: &RepoAnalysis,
    recipe: &DemoRecipe,
    work_dir: Option<&Path>,
    hooks: DeployHooks,
) -> HostResult {
    let emit = |msg: &str| {
        if let Some(cb) = hooks.on_status {
            cb(msg);
        }
    };
    let mut result = HostResult::default();
    let report_err = |result: &mut HostResult, msg: String| {
        eprintln!("{msg}");
        result.errors.push(msg);
    };

    emit("cloning repository...");
    let Some(project_dir) = clone_repo(analysis, work_dir) else {
        result
            .errors
            .push("git clone failed after retries (check network connection)".into());
        return result;
    };

    // ── build ──
    for step in recipe.pre_build.iter().chain(recipe.build_steps.iter()) {
        emit(&format!("build: {}", short_cmd(step)));
        match run_build_cmd(step, &project_dir, None) {
            Ok(r) if r.success => {}
            Ok(r) => {
                report_err(
                    &mut result,
                    format!("Build step failed ({step}):\n{}", short(&r.stderr)),
                );
                return result;
            }
            Err(e) => {
                report_err(&mut result, format!("Build step failed ({step}): {e}"));
                return result;
            }
        }
    }

    // ── install ──
    let port = match find_free_port(recipe.port, 50) {
        Ok(p) => p,
        Err(e) => {
            report_err(&mut result, e.to_string());
            return result;
        }
    };
    if recipe.searxng {
        prepare_searxng_config(&project_dir, port);
    }
    let exec_start = resolve_start(recipe, &project_dir, port);

    emit(&format!(
        "installing systemd unit {}...",
        recipe.service_name
    ));
    stop_existing(recipe.service_name);

    let env_map: BTreeMap<String, String> = BTreeMap::new(); // demo recipes carry no secrets
    let env_file = write_env_file(recipe.service_name, &env_map).ok().flatten();

    let spec = UnitSpec {
        service_name: recipe.service_name,
        working_dir: &project_dir,
        exec_start: &exec_start,
        description: &format!("demo: {}", recipe.description),
        env_file: env_file.as_deref(),
        extra_env: &[],
    };
    if let Err(e) = create_unit(&spec) {
        report_err(&mut result, format!("unit creation failed: {e:#}"));
        return result;
    }

    // ── start + verify (polling; see units.rs / FINDINGS.md) ──
    emit("starting service...");
    let started = Command::new("systemctl")
        .args(["--user", "start", "--no-block", recipe.service_name])
        .status();
    if started.is_err() {
        report_err(&mut result, "failed to invoke systemctl start".into());
        cleanup_failed(&mut result, recipe.service_name);
        return result;
    }

    match wait_until_active(recipe.service_name) {
        StartOutcome::Active => {}
        StartOutcome::Failed => {
            let logs = service_logs(recipe.service_name, 20);
            report_err(
                &mut result,
                format!("Service crashed immediately after start:\n{}", short(&logs)),
            );
            cleanup_failed(&mut result, recipe.service_name);
            return result;
        }
        StartOutcome::TimeoutWhileActivating => {
            let logs = service_logs(recipe.service_name, 20);
            report_err(
                &mut result,
                format!(
                    "Service did not become active within {}s:\n{}",
                    super::units::START_BUDGET.as_secs(),
                    short(&logs)
                ),
            );
            cleanup_failed(&mut result, recipe.service_name);
            return result;
        }
        StartOutcome::SystemdUnavailable => {
            report_err(&mut result, "systemd user manager unavailable".into());
            return result;
        }
    }

    crate::state::register(
        recipe.service_name,
        crate::state::ServiceEntry {
            unit_name: recipe.service_name.into(),
            project_dir: project_dir.to_string_lossy().into_owned(),
            url: analysis.url.clone(),
            urls: vec![format!("http://localhost:{port}")],
        },
    )
    .context("registering state")
    .ok();

    result.service_names = vec![recipe.service_name.into()];
    result.urls = vec![format!("http://localhost:{port}")];
    result
}

fn cleanup_failed(result: &mut HostResult, service: &str) {
    for name in &result.service_names.clone() {
        remove_unit(name);
        super::secrets::remove_env_file(name);
    }
    remove_unit(service);
    super::secrets::remove_env_file(service);
    let _ = Command::new("systemctl")
        .args(["--user", "daemon-reload"])
        .status();
}

/// Remove a clone after an aborted deploy.
pub fn cleanup_clone(clone_path: &str) {
    let _ = std::fs::remove_dir_all(clone_path);
}

/// Stop, delete the unit and forget the service (used by "My Services").
pub fn remove_unit_and_state(service_name: &str) {
    remove_unit(service_name);
    super::secrets::remove_env_file(service_name);
    crate::state::unregister(service_name).ok();
    let _ = Command::new("systemctl")
        .args(["--user", "daemon-reload"])
        .status();
}

fn short(s: &str) -> String {
    s.chars().take(300).collect()
}

fn short_cmd(s: &str) -> String {
    s.chars().take(100).collect()
}
