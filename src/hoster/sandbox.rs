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

const DEFAULT_TIMEOUT: Duration = Duration::from_secs(900);

pub struct CmdResult {
    pub success: bool,
    pub stdout: String,
    pub stderr: String,
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

fn sandbox_enabled() -> bool {
    !matches!(
        std::env::var("GHOSTPROVIDER_NO_SANDBOX").as_deref(),
        Ok("1" | "true" | "yes" | "on")
    )
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
pub fn run_sandboxed(argv: &[String], cwd: &Path, timeout: Duration) -> anyhow::Result<CmdResult> {
    let mut run_env: BTreeMap<String, String> = std::env::vars().collect();
    let cache = cache_env(Some(cwd));
    for (k, v) in &cache {
        run_env.insert((*k).to_string(), v.clone());
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

    let unit = format!("ghost-build-{}.service", uuid_short());
    let mut args: Vec<String> = vec![
        "--user".into(),
        "--wait".into(),
        "--pipe".into(),
        "--collect".into(),
        "--unit".into(),
        unit.clone(),
        format!("--working-directory={}", cwd.display()),
    ];
    for prop in SANDBOX_PROPERTIES {
        args.push(format!("--property={prop}"));
    }
    args.push(format!("--property=ReadWritePaths={}", cwd.display()));
    for (k, v) in &run_env {
        args.push(format!("--setenv={k}={v}"));
    }
    args.push("--".into());
    args.extend(argv.iter().cloned());

    let res = Command::new("systemd-run").args(&args).output();

    match res {
        Err(_) => run_plain(argv, cwd, &run_env, timeout),
        Ok(out) => {
            let stderr = String::from_utf8_lossy(&out.stderr);
            let started = stderr.to_lowercase().contains("running as unit");
            if !out.status.success() && !started {
                // Unit never ran (no user manager / DBus): execute directly.
                return run_plain(argv, cwd, &run_env, timeout);
            }
            Ok(CmdResult {
                success: out.status.success(),
                stdout: String::from_utf8_lossy(&out.stdout).into_owned(),
                stderr: strip_status_preamble(&stderr),
            })
        }
    }
}

/// Run a shell snippet (`sh -c`) after the heuristic validation pass.
pub fn run_build_cmd(
    cmd: &str,
    project_dir: &Path,
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
    )
}

fn run_plain(
    argv: &[String],
    cwd: &Path,
    env: &BTreeMap<String, String>,
    timeout: Duration,
) -> anyhow::Result<CmdResult> {
    use std::io::Read;
    let mut child = Command::new(&argv[0])
        .args(&argv[1..])
        .current_dir(cwd)
        .env_clear()
        .envs(env)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()?;

    let deadline = std::time::Instant::now() + timeout;
    loop {
        if let Some(status) = child.try_wait()? {
            let mut out = String::new();
            let mut err = String::new();
            let _ = child.stdout.take().unwrap().read_to_string(&mut out);
            let _ = child.stderr.take().unwrap().read_to_string(&mut err);
            return Ok(CmdResult {
                success: status.success(),
                stdout: out,
                stderr: err,
            });
        }
        if std::time::Instant::now() > deadline {
            let _ = child.kill();
            anyhow::bail!("command timed out after {}s", timeout.as_secs());
        }
        std::thread::sleep(Duration::from_millis(100));
    }
}

fn which(bin: &str) -> bool {
    std::env::var_os("PATH")
        .map(|p| std::env::split_paths(&p).any(|dir| dir.join(bin).is_file()))
        .unwrap_or(false)
}

fn uuid_short() -> u32 {
    let d = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default();
    d.subsec_nanos() ^ (d.as_secs() as u32) ^ std::process::id()
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
