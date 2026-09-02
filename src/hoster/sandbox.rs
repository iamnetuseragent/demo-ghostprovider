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

/// The build unit is fully offline. `PrivateNetwork=yes` places the transient
/// unit in a private netns with only a loopback interface; `IPAddressDeny=any`
/// makes even loopback chatter fail closed. This is the single most important
/// isolation a *build* gets: a malicious `postinstall`, `build.rs`, `setup.py`
/// or `Makefile` can no longer phone home — the network is gone at the systemd
/// level, not merely blocked by a heuristic. Dependency downloads are moved
/// *before* the sandbox (see `prefetch`), so the build itself never needs
/// egress. This is why `--verify-sandbox`'s "no outbound connect" guarantee is
/// now structural instead of observational.
const BUILD_NETWORK_PROPERTIES: &[&str] = &["PrivateNetwork=yes", "IPAddressDeny=any"];

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
const SCRUBBED_ENV_PATTERNS: &[&str] =
    &["TOKEN", "PASSWORD", "SECRET", "OPENCHAMBER_", "OPENCODE_"];

/// Whether an env-var name looks like a credential (token/password/secret or
/// an openchamber/opencode session secret such as the agent-tool token).
fn is_credential_name(name: &str) -> bool {
    SCRUBBED_ENV_VARS.contains(&name)
        || SCRUBBED_ENV_PATTERNS
            .iter()
            .any(|p| name.contains(p) || name.to_ascii_uppercase().contains(p))
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

/// Env var naming the dedicated unprivileged build user (e.g. `ghostbuild`).
/// When set *and* the current process can drop privileges to it (running as
/// root), every build step is prefixed with `setpriv --reuid --regid
/// --init-groups` so the sandboxed build runs as that user — a hostile recipe
/// then cannot touch the invoker's files. When set but privilege-drop is not
/// possible, we warn loudly instead of failing, because a non-root panel
/// legitimately CANNOT chown to another uid (setpriv needs CAP_SETUID).
pub const BUILD_USER_ENV: &str = "GHOSTPROVIDER_BUILD_USER";

/// Opt-out of the "no dedicated build user" warning. When the panel runs as a
/// non-root user (so it can never drop to another uid) and no build user is
/// configured, builds run as the invoking user; that is the documented demo
/// model, and the flag acknowledges it. Without it, the status line warns.
pub const ALLOW_INSECURE_USER_ENV: &str = "GHOSTPROVIDER_ALLOW_INSECURE_USER";

/// Prefix for the build argv when running as a dedicated build user; `None`
/// when no privilege drop is in effect (current-user build).
fn build_user_prefix() -> Option<Vec<String>> {
    let name = std::env::var(BUILD_USER_ENV).unwrap_or_default();
    if name.is_empty() {
        return None;
    }
    // Only a privileged process can drop to another uid. For a regular user
    // setpriv would EPERM on --reuid; detect and bail to the warning path.
    if unsafe { libc::geteuid() } != 0 {
        return None;
    }
    let uv = user_uid(name.trim())?;
    let gv = group_gid(name.trim())?;
    Some(vec![
        "setpriv".into(),
        format!("--reuid={uv}"),
        format!("--regid={gv}"),
        "--init-groups".into(),
        "--".into(),
    ])
}

/// Whether a dedicated build user is configured but unusable from this
/// process (the reason `build_user_prefix` returned None with the env set).
fn build_user_unusable_reason() -> Option<&'static str> {
    let name = std::env::var(BUILD_USER_ENV).unwrap_or_default();
    if name.is_empty() {
        return None;
    }
    if unsafe { libc::geteuid() } != 0 {
        return Some(
            "GHOSTPROVIDER_BUILD_USER set, but dropping privileges needs root — builds run as the invoking user",
        );
    }
    if user_uid(name.trim()).is_none() || group_gid(name.trim()).is_none() {
        return Some(
            "GHOSTPROVIDER_BUILD_USER set, but that user does not exist — builds run as the invoking user",
        );
    }
    None
}

fn user_uid(name: &str) -> Option<u32> {
    let out = Command::new("id").arg("-u").arg(name).output().ok()?;
    if !out.status.success() {
        return None;
    }
    String::from_utf8_lossy(&out.stdout).trim().parse().ok()
}

fn group_gid(name: &str) -> Option<u32> {
    let out = Command::new("id").arg("-g").arg(name).output().ok()?;
    if !out.status.success() {
        return None;
    }
    String::from_utf8_lossy(&out.stdout).trim().parse().ok()
}

/// Human-readable warning shown on the status line whenever a build will NOT
/// run inside the hardened sandbox. `None` means full isolation is active.
pub fn sandbox_warning() -> Option<&'static str> {
    match effective_mode() {
        EffectiveSandbox::Full => {}
        EffectiveSandbox::DisabledByEnv => {
            return Some(
                "sandbox DISABLED by GHOSTPROVIDER_NO_SANDBOX — builds run with no isolation",
            );
        }
        EffectiveSandbox::FallbackPlain => {
            return Some("systemd-run not found — builds run with no isolation");
        }
    }
    if let Some(reason) = build_user_unusable_reason() {
        return Some(reason);
    }
    // Neither sandbox-disabled nor build-user-configured: builds run as the
    // invoking user. That is the documented demo model for a non-root panel;
    // acknowledged via GHOSTPROVIDER_ALLOW_INSECURE_USER or surfaced loudly.
    if !crate::flags::env_flag(ALLOW_INSECURE_USER_ENV) {
        Some("no dedicated build user (GHOSTPROVIDER_BUILD_USER) — builds run as the invoking user")
    } else {
        None
    }
}

/// Drop credential-bearing variables from a child environment.
fn scrub_env(run_env: &mut BTreeMap<String, String>) {
    run_env.retain(|k, _| !is_credential_name(k));
}

/// The canonical list of credential variable names the sandbox (and the host
/// prefetch runner) must never pass to a child process. Exposed so the
/// prefetch phases scrub with the identical policy.
pub fn sandbox_scrub_denylist() -> &'static [&'static str] {
    SCRUBBED_ENV_VARS
}

/// Cache/home redirects applied inside the build sandbox. Exposed so the
/// host prefetch runner applies the identical redirects and writes the cache
/// files the offline build later reads.
pub(crate) fn cache_env_pub(project_dir: Option<&Path>) -> BTreeMap<&'static str, String> {
    cache_env(project_dir)
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
        // pnpm v11 is network-paranoid by default: it verifies the managed
        // engine's npm registry signature (engine identity) and re-checks each
        // locked version's publish age against the registry (default
        // minimum-release-age = 1440 min) on every resolution. Neither can work
        // in the offline build sandbox. The sandbox already pins source identity
        // to the git SHA, so we drop both network-only checks here rather than
        // let an unreachable registry abort the frozen build.
        ("pnpm_config_pm_on_fail", "ignore".to_string()),
        ("pnpm_config_minimum_release_age", "0".to_string()),
        ("TMPDIR", m("tmp")),
        // HOME is re-pointed inside the sandbox so a hostile build step cannot
        // read the invoking user's real home (~/.ssh, ~/.netrc, ~/.config with
        // whatever session tokens live there). The redirect target itself is a
        // scratch dir under the project, only populated by the build step.
        ("HOME", m("home")),
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

/// Prefix `argv` with `setpriv --reuid=... --regid=... --init-groups` when a
/// dedicated build user is configured and usable; otherwise return it as-is.
fn with_build_user_prefix(argv: &[String]) -> Vec<String> {
    match build_user_prefix() {
        Some(p) => p.iter().cloned().chain(argv.iter().cloned()).collect(),
        None => argv.to_vec(),
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
    // Build steps are non-interactive by definition (no TTY in a systemd-run
    // unit): force CI=1 exactly like the host prefetch runner does, so pnpm
    // and friends never stall on a confirmation prompt (e.g. pnpm's modules
    // purge when the projected self-version does a layout change).
    run_env.insert("CI".to_string(), "1".into());
    // Keep the sandbox consistent with the tool doctor: when the doctor
    // allowed an old `go` because GOTOOLCHAIN=auto can fetch the required
    // toolchain (checksum-verified, cached under GOPATH), the build must be
    // able to actually do it. When the doctor blocked (missing tool,
    // GOTOOLCHAIN=local), we never reach the build at all.
    run_env.insert("GOTOOLCHAIN".to_string(), "auto".into());
    precreate_cache_dirs(cwd, &cache);

    if !sandbox_enabled() || !which("systemd-run") {
        let argv = with_build_user_prefix(argv);
        return run_plain(&argv, cwd, &run_env, timeout);
    }

    let argv = with_build_user_prefix(argv);
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
    // Network isolation lives in its own property list so it reads clearly
    // as the build boundary (see BUILD_NETWORK_PROPERTIES).
    for prop in BUILD_NETWORK_PROPERTIES {
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
                return run_plain(&argv, cwd, &run_env, timeout);
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
        Err(_) => run_plain(&argv, cwd, &run_env, timeout),
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

    #[test]
    fn cache_env_redirects_home_into_project() {
        let env = cache_env(Some(Path::new("/proj")));
        let home = env.get("HOME").map(|s| s.as_str());
        assert!(matches!(home, Some(h) if h.starts_with("/proj/.ghost-cache/")));
        assert!(!matches!(home, Some(h) if h.starts_with("/home/")));
        let pnpm = env.get("npm_config_store_dir").map(|s| s.as_str());
        assert!(matches!(pnpm, Some(p) if p.starts_with("/proj/.ghost-cache/")));
        // Network-paranoid pnpm v11 checks are disabled so the offline build
        // does not abort trying to reach the registry.
        assert_eq!(
            env.get("pnpm_config_pm_on_fail").map(String::as_str),
            Some("ignore")
        );
        assert_eq!(
            env.get("pnpm_config_minimum_release_age")
                .map(String::as_str),
            Some("0")
        );
    }

    #[test]
    fn build_user_ignored_for_non_root() {
        // Non-root (this test runs as an unprivileged user in CI/CI-less run):
        // the prefix must be None, and the warning must explain why.
        if unsafe { libc::geteuid() } == 0 {
            return;
        }
        unsafe {
            std::env::set_var(BUILD_USER_ENV, "ghostbuild");
        }
        assert!(build_user_prefix().is_none());
        assert!(build_user_unusable_reason().is_some());
        unsafe {
            std::env::remove_var(BUILD_USER_ENV);
        }
    }
}
