//! Sandboxed command execution via `systemd-run --user`.
//!
//! Build/install steps run inside a transient user unit with hardening
//! properties (strict filesystem protection outside the project dir, no new
//! privileges, no devices, empty capability set). Tool caches are redirected
//! under `<project>/.ghost-cache` and persist between deployments.
//!
//! HONESTY NOTE (do not remove): this is a blast-radius reducer, NOT a trust
//! boundary — the build runs as the same user. See README threat model.

use std::collections::BTreeMap;
use std::path::Path;
use std::process::Command;
use std::time::Duration;

const SANDBOX_PROPERTIES: &[&str] = &[
    "NoNewPrivileges=yes",
    // No PrivateTmp: shadows /tmp and breaks builds whose workdir is under
    // /tmp; tool temp files are redirected via TMPDIR below anyway.
    "ProtectSystem=strict",
    "ProtectHome=read-only",
    "PrivateDevices=yes",
    "ProtectControlGroups=yes",
    "ProtectKernelTunables=yes",
    "ProtectKernelModules=yes",
    "RestrictNamespaces=yes",
    "LockPersonality=yes",
    "RestrictRealtime=yes",
    "RestrictSUIDSGID=yes",
    "CapabilityBoundingSet=",
];

/// Per build-step budget. Generous because `bun install`/`pnpm install` on a
/// throttled link can legitimately need many minutes to pull a large
/// node_modules, and a `go build` may first bootstrap a missing toolchain via
/// `GOTOOLCHAIN=auto` (~70 MiB fetch at link speeds measured here). Truly-hung
/// steps are still reaped by RuntimeMaxSec.
const DEFAULT_TIMEOUT: Duration = Duration::from_secs(90 * 60);

/// Env vars a build step must never inherit. The sandbox already constrains
/// filesystem reach, but env vars are how a hostile build step would
/// exfiltrate a deployment credential (GITHUB_TOKEN/GH_TOKEN are set by the
/// user for clone rate limits and are ubiquitous ambient CI secrets).
const SCRUBBED_ENV_VARS: &[&str] = &[
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "NPM_TOKEN",
    "NODE_AUTH_TOKEN",
    "DOCKER_AUTH_CONFIG",
    "BUN_AUTH_TOKEN",
];

/// Variable-name patterns that identify credentials regardless of their exact
/// name, so a future session secret cannot slip through a stale denylist.
const SCRUBBED_ENV_PATTERNS: &[&str] = &["TOKEN", "PASSWORD", "SECRET", "OPENCHAMBER_", "OPENCODE_"];

/// Whether an env-var name looks like a credential (token/password/secret or
/// an openchamber/opencode session secret such as the agent-tool token).
fn is_credential_name(name: &str) -> bool {
    SCRUBBED_ENV_VARS.contains(&name)
        || SCRUBBED_ENV_PATTERNS.iter().any(|p| {
            name.contains(p) || name.to_ascii_uppercase().contains(p)
        })
}

pub struct CmdResult {
    pub success: bool,
    pub stdout: String,
    pub stderr: String,
}

/// Whether the hardened isolation is actually in effect for build steps.
/// The user may disable it explicitly (`GHOSTPROVIDER_NO_SANDBOX=1`), and
/// `systemd-run` may be missing at runtime — both must surface as a warning,
/// never silently reduce protection.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EffectiveSandbox {
    Full,
    DisabledByEnv,
    FallbackPlain,
}

fn sandbox_enabled() -> bool {
    !crate::flags::env_flag("GHOSTPROVIDER_NO_SANDBOX")
}

pub fn effective_mode() -> EffectiveSandbox {
    if !sandbox_enabled() {
        EffectiveSandbox::DisabledByEnv
    } else if which("systemd-run") {
        EffectiveSandbox::Full
    } else {
        EffectiveSandbox::FallbackPlain
    }
}

/// Human-readable warning shown on the status line whenever a build will NOT
/// run inside the hardened sandbox. `None` means full isolation is active.
pub fn sandbox_warning() -> Option<&'static str> {
    match effective_mode() {
        EffectiveSandbox::Full => None,
        EffectiveSandbox::DisabledByEnv => {
            Some("sandbox DISABLED by GHOSTPROVIDER_NO_SANDBOX — builds run with no isolation")
        }
        EffectiveSandbox::FallbackPlain => {
            Some("systemd-run not found — builds run with no isolation")
        }
    }
}

/// Drop credential-bearing variables from a child environment.
fn scrub_env(run_env: &mut BTreeMap<String, String>) {
    run_env.retain(|k, _| !is_credential_name(k));
}

fn cache_env(project_dir: Option<&Path>) -> BTreeMap<&'static str, String> {
    let Some(dir) = project_dir else {
        return BTreeMap::new();
    };
    let base = dir.join(".ghost-cache");
    let m = |sub: &str| base.join(sub).to_string_lossy().into_owned();
    BTreeMap::from([
        ("XDG_CACHE_HOME", m("xdg")),
        ("npm_config_cache", m("npm")),
        ("YARN_CACHE_FOLDER", m("yarn")),
        ("BUN_INSTALL_CACHE_DIR", m("bun")),
        ("CARGO_HOME", m("cargo")),
        ("GOCACHE", m("go")),
        ("GOMODCACHE", m("go-mod")),
        ("GOPATH", m("go-path")),
        ("GOTMPDIR", m("go-tmp")),
        ("npm_config_store_dir", m("pnpm")),
        ("PNPM_HOME", m("pnpm-home")),
        ("TMPDIR", m("tmp")),
    ])
}

fn precreate_cache_dirs(project_dir: &Path, env: &BTreeMap<&'static str, String>) {
    if env.is_empty() {
        return;
    }
    let _ = std::fs::create_dir_all(project_dir.join(".ghost-cache"));
    for p in env.values() {
        let _ = std::fs::create_dir_all(p);
    }
}

/// Run `argv` inside the hardened sandbox; falls back to plain execution when
/// systemd-run is unavailable or the user manager rejects the unit.
pub fn run_sandboxed(
    argv: &[String],
    cwd: &Path,
    timeout: Duration,
    extra_env: &[(String, String)],
) -> anyhow::Result<CmdResult> {
    let mut run_env: BTreeMap<String, String> = std::env::vars().collect();
    // A hostile project's build step would inherit every credential in the
    // environment; scrub them before the git-clone rates-only token can leak.
    scrub_env(&mut run_env);
    let cache = cache_env(Some(cwd));
    for (k, v) in &cache {
        run_env.insert((*k).to_string(), v.clone());
    }
    // Explicit overrides (e.g. the toolchain file:// GOPROXY from goenv)
    // win over any ambient ones — they are not credentials and go through
    // the same 0600-backed cache directory as the tool caches.
    for (k, v) in extra_env {
        run_env.insert(k.clone(), v.clone());
    }
    // Keep the sandbox consistent with the tool doctor: when the doctor
    // allowed an old `go` because GOTOOLCHAIN=auto can fetch the required
    // toolchain (checksum-verified, cached under GOPATH), the build must be
    // able to actually do it. When the doctor blocked (missing tool,
    // GOTOOLCHAIN=local), we never reach the build at all.
    run_env.insert("GOTOOLCHAIN".to_string(), "auto".into());
    precreate_cache_dirs(cwd, &cache);

    if !sandbox_enabled() || !which("systemd-run") {
        return run_plain(argv, cwd, &run_env, timeout);
    }

    let unit = format!(
        "ghost-build-{}.service",
        crate::atomic::random_hex(4).unwrap_or_default()
    );
    // Paths go through the same unit-file escaping as the service units, and
    // the ReadWritePaths list value is quoted: a cwd containing spaces or %
    // must not break the transient unit.
    let cwd_escaped = super::units::escape_unit_value(&cwd.to_string_lossy());
    let mut args: Vec<String> = vec![
        "--user".into(),
        "--wait".into(),
        "--pipe".into(),
        "--collect".into(),
        "--unit".into(),
        unit.clone(),
        format!("--working-directory={cwd_escaped}"),
    ];
    for prop in SANDBOX_PROPERTIES {
        args.push(format!("--property={prop}"));
    }
    args.push(format!("--property=ReadWritePaths=\"{cwd_escaped}\""));
    // Hard cap the transient unit's lifetime. `--wait` alone would block
    // forever on a hung build, and killing only systemd-run would orphan the
    // unit; RuntimeMaxSec lets systemd reap it at the deadline.
    args.push(format!("--property=RuntimeMaxSec={}", timeout.as_secs()));
    for (k, v) in &run_env {
        args.push(format!("--setenv={k}={v}"));
    }
    args.push("--".into());
    args.extend(argv.iter().cloned());

    let mut sysrun: Vec<String> = vec!["systemd-run".into()];
    sysrun.extend(args);
    // Grace so systemd-run can observe the RuntimeMaxSec kill and exit on its
    // own; only when it is still wedged do we kill it directly.
    let grace = timeout + Duration::from_secs(30);
    match run_cmd_timed(&sysrun, cwd, &run_env, grace) {
        Ok((status, stdout, stderr)) => {
            let started = stderr.to_lowercase().contains("running as unit");
            if !status.map(|s| s.success()).unwrap_or(false) && !started {
                // Unit never ran (no user manager / DBus): execute directly.
                return run_plain(argv, cwd, &run_env, timeout);
            }
            let stderr = strip_status_preamble(&stderr);
            let stderr = if is_timeout_result(&stderr) {
                TimedOut(timeout.as_secs()).to_string()
            } else {
                stderr
            };
            Ok(CmdResult {
                success: status.map(|s| s.success()).unwrap_or(false),
                stdout,
                stderr,
            })
        }
        // An explicit timeout is a hard failure — do NOT fall back to plain
        // execution (which would only re-run the same hung build).
        Err(e) if e.is::<TimedOut>() => Err(e),
        Err(_) => run_plain(argv, cwd, &run_env, timeout),
    }
}

/// systemd-run reports a unit killed by its time limit as
/// `Finished with result: timeout`. Surface that as the plain message instead
/// of letting the stripped preamble swallow the reason.
fn is_timeout_result(stderr: &str) -> bool {
    let lower = stderr.to_lowercase();
    lower.contains("result: timeout") || lower.contains("result 'timeout'")
}

/// Run a shell snippet (`sh -c`) after the heuristic validation pass.
pub fn run_build_cmd(
    cmd: &str,
    project_dir: &Path,
    extra_env: &[(String, String)],
    on_status: Option<&dyn Fn(&str)>,
) -> anyhow::Result<CmdResult> {
    super::validate::validate_build_cmd(cmd)
        .map_err(|reason| anyhow::anyhow!("{reason}: {cmd}"))?;
    if let Some(cb) = on_status {
        cb(&format!(
            "build: {}",
            cmd.chars().take(120).collect::<String>()
        ));
    }
    run_sandboxed(
        &["/bin/sh".into(), "-c".into(), cmd.to_string()],
        project_dir,
        DEFAULT_TIMEOUT,
        extra_env,
    )
}

/// Deadline exceeded. Bubbles up as a hard error (never a silent retry).
#[derive(Debug)]
struct TimedOut(u64);

impl std::fmt::Display for TimedOut {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "command timed out after {}s", self.0)
    }
}

impl std::error::Error for TimedOut {}

/// Spawn `argv` with a cleared environment, stream stdout/stderr through
/// background reader threads (so output beyond the pipe buffer cannot
/// deadlock the wait) and reap (or kill) the child within `timeout`.
fn run_cmd_timed(
    argv: &[String],
    cwd: &Path,
    env: &BTreeMap<String, String>,
    timeout: Duration,
) -> anyhow::Result<(Option<std::process::ExitStatus>, String, String)> {
    use std::io::Read;

    let mut child = Command::new(&argv[0])
        .args(&argv[1..])
        .current_dir(cwd)
        .env_clear()
        .envs(env)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()?;
    let out_pipe = child.stdout.take().unwrap();
    let err_pipe = child.stderr.take().unwrap();
    let out_th = std::thread::spawn(move || {
        let mut s = String::new();
        let _ = std::io::BufReader::new(out_pipe).read_to_string(&mut s);
        s
    });
    let err_th = std::thread::spawn(move || {
        let mut s = String::new();
        let _ = std::io::BufReader::new(err_pipe).read_to_string(&mut s);
        s
    });

    let deadline = std::time::Instant::now() + timeout;
    loop {
        match child.try_wait()? {
            Some(status) => {
                let out = out_th.join().unwrap_or_default();
                let err = err_th.join().unwrap_or_default();
                return Ok((Some(status), out, err));
            }
            None => {
                if std::time::Instant::now() > deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    let _ = out_th.join();
                    let _ = err_th.join();
                    anyhow::bail!(TimedOut(timeout.as_secs()));
                }
                std::thread::sleep(Duration::from_millis(100));
            }
        }
    }
}

fn run_plain(
    argv: &[String],
    cwd: &Path,
    env: &BTreeMap<String, String>,
    timeout: Duration,
) -> anyhow::Result<CmdResult> {
    let (status, out, err) = run_cmd_timed(argv, cwd, env, timeout)?;
    Ok(CmdResult {
        success: status.map(|s| s.success()).unwrap_or(false),
        stdout: out,
        stderr: err,
    })
}

fn which(bin: &str) -> bool {
    std::env::var_os("PATH")
        .map(|p| std::env::split_paths(&p).any(|dir| dir.join(bin).is_file()))
        .unwrap_or(false)
}

fn strip_status_preamble(err: &str) -> String {
    let mut lines: Vec<&str> = Vec::new();
    for line in err.lines() {
        let lower = line.trim().to_lowercase();
        if lower.starts_with("running as unit:") || lower.starts_with("finished with result:") {
            continue;
        }
        lines.push(line);
    }
    lines.join("\n").trim().to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scrub_removes_all_credential_vars() {
        let mut env: BTreeMap<String, String> = BTreeMap::from([
            ("GITHUB_TOKEN".into(), "t1".into()),
            ("GH_TOKEN".into(), "t2".into()),
            ("NPM_TOKEN".into(), "t3".into()),
            ("NODE_AUTH_TOKEN".into(), "t4".into()),
            ("DOCKER_AUTH_CONFIG".into(), "t5".into()),
            ("BUN_AUTH_TOKEN".into(), "t6".into()),
            ("OPENCHAMBER_AGENT_TOOL_TOKEN".into(), "t7".into()),
            ("OPENCODE_SERVER_PASSWORD".into(), "t8".into()),
            ("npm_config__authToken".into(), "t9".into()),
            ("PATH".into(), "/usr/bin".into()),
            ("HOME".into(), "/home/u".into()),
        ]);
        scrub_env(&mut env);
        assert!(!env.contains_key("GITHUB_TOKEN"));
        assert!(!env.contains_key("GH_TOKEN"));
        assert!(!env.contains_key("NPM_TOKEN"));
        assert!(!env.contains_key("NODE_AUTH_TOKEN"));
        assert!(!env.contains_key("DOCKER_AUTH_CONFIG"));
        assert!(!env.contains_key("BUN_AUTH_TOKEN"));
        assert!(!env.contains_key("OPENCHAMBER_AGENT_TOOL_TOKEN"));
        assert!(!env.contains_key("OPENCODE_SERVER_PASSWORD"));
        assert!(!env.contains_key("npm_config__authToken"));
        // Benign vars survive untouched.
        assert_eq!(env.get("PATH"), Some(&"/usr/bin".to_string()));
        assert_eq!(env.get("HOME"), Some(&"/home/u".to_string()));
    }

    #[test]
    fn timeout_error_is_typed_not_retried() {
        let err = anyhow::anyhow!(TimedOut(5));
        assert!(err.is::<TimedOut>());
        assert_eq!(err.to_string(), "command timed out after 5s");
    }

    #[test]
    fn timeout_result_detected() {
        assert!(is_timeout_result("Finished with result: timeout"));
        assert!(is_timeout_result("Failed with result 'timeout'."));
        assert!(!is_timeout_result("curl: (28) Operation timed out"));
        assert!(!is_timeout_result("OK"));
    }
}
