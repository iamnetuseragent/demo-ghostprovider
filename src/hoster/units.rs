//! systemd user unit generation and service start verification.
//!
//! Start verification polls `systemctl --user is-active` instead of the
//! Python version's single check after `sleep 5`. Measured on systemd 261
//! (FINDINGS.md):
//!
//! * `activating` and `failed` both exit with rc=**3** — the state string is
//!   the only reliable signal; exit codes must not be used.
//! * A slow service stays `activating` well past 5s, so a single late check
//!   produced false "crashed" reports.

use std::path::Path;
use std::process::Command;
use std::time::{Duration, Instant};

use anyhow::Context;

const POLL_INTERVAL: Duration = Duration::from_millis(500);
pub const START_BUDGET: Duration = Duration::from_secs(30);

/// Validate that a string is a safe systemd unit name.
pub fn validate_unit_name(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 255
        && name
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '-' | '.'))
        && !name.starts_with('.')
}

/// Sanitize an arbitrary string into a safe unit-name fragment.
pub fn sanitize_service_name(name: &str) -> String {
    if validate_unit_name(name) {
        return name.to_string();
    }
    let sanitized: String = name
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || matches!(c, '_' | '-' | '.') {
                c
            } else {
                '-'
            }
        })
        .collect();
    sanitized.chars().take(64).collect()
}

/// Escape a value for use *inside the unit file* (Environment=/ExecStart=).
/// In this context — unlike EnvironmentFile (see secrets.rs) — `$` and `%`
/// ARE special: `$$` → literal `$`, `%%` → literal `%`.
pub fn escape_unit_value(value: &str) -> String {
    value
        .replace('\\', "\\\\")
        .replace('\n', "\\n")
        .replace('"', "\\\"")
        .replace('$', "$$")
        .replace('%', "%%")
}

/// Quote every whitespace-separated argument of an ExecStart command line so
/// values containing spaces (e.g. a HOME path) survive systemd's splitter.
/// Verified against systemd 261 with `systemd-analyze verify`.
pub fn quote_exec_args(cmd: &str) -> String {
    cmd.split_whitespace()
        .map(|word| format!("\"{}\"", escape_unit_value(word)))
        .collect::<Vec<_>>()
        .join(" ")
}

fn systemctl(args: &[&str]) -> Option<(bool, String)> {
    let out = Command::new("systemctl")
        .arg("--user")
        .args(args)
        .output()
        .ok()?;
    Some((
        out.status.success(),
        String::from_utf8_lossy(&out.stdout).trim().to_string(),
    ))
}

/// Optional systemd resource-limit knobs rendered into a unit. Values are
/// literal directives (e.g. `"512M"`, `"300"`); `None` leaves systemd's
/// defaults in place. A demo service getting away with unbounded memory/tasks
/// can grind the whole user session to a halt, so every recipe opts into a
/// tight budget (see `DemoRecipe::res`); `none()` is reserved for the
/// selftest path, which never runs a real service.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct ResourceLimits {
    /// `MemoryHigh=` — memory soft cap (reclaim pressure first).
    pub memory_high: Option<&'static str>,
    /// `MemoryMax=` — hard OOM ceiling for the unit's cgroup.
    pub memory_max: Option<&'static str>,
    /// `TasksMax=` — process/thread count cap for the unit's cgroup.
    pub tasks_max: Option<&'static str>,
    /// `CPUQuota=` — CPU-time cap for the unit's cgroup (e.g. `"100%"` for one
    /// core). A runaway service must not pin every core of the session; the
    /// build units already cap CPU via `BUILD_RESOURCE_PROPERTIES`.
    pub cpu_quota: Option<&'static str>,
    /// `LimitNOFILE=` — file-descriptor cap (applied to soft and hard).
    pub limit_nofile: Option<u32>,
    /// `OOMScoreAdjust=` — OOM-killer bias vs sibling units.
    pub oom_score_adjust: Option<i32>,
}

impl ResourceLimits {
    pub const fn none() -> Self {
        Self {
            memory_high: None,
            memory_max: None,
            tasks_max: None,
            cpu_quota: None,
            limit_nofile: None,
            oom_score_adjust: None,
        }
    }
}

/// Write the hardened user unit for `service_name` and daemon-reload.
///
/// Hardening set mirrors the Python version: no new privileges, read-only
/// home + inaccessible invoker secret roots, strict-ish filesystem
/// protection with a writable working dir, kernel/control-group locks,
/// seccomp deny-list, empty capability bounding set, per-recipe
/// CPU/memory/tasks caps, loopback-only when applicable.
pub struct UnitSpec<'a> {
    pub service_name: &'a str,
    pub working_dir: &'a Path,
    pub exec_start: &'a str,
    pub description: &'a str,
    pub env_file: Option<&'a Path>,
    pub extra_env: &'a [(String, String)],
    /// Recipe's runtime needs no outbound network (vert's built-in static
    /// server): lock the unit to loopback so a compromised build output can
    /// never phone home.
    pub loopback_only: bool,
    /// Resource caps rendered into the unit (see `ResourceLimits`).
    pub res: ResourceLimits,
}

pub fn create_unit(spec: &UnitSpec) -> anyhow::Result<()> {
    let service_name = sanitize_service_name(spec.service_name);
    let unit_dir = crate::paths::user_unit_dir();
    std::fs::create_dir_all(&unit_dir)?;

    let content = render_unit(spec)?;

    // Pre-create the project-scoped cache/home/runtime dirs the unit's env
    // redirects to, so a service always sees writable HOME/XDG_* locations
    // that survive restarts.
    let cache_root = spec.working_dir.join(".ghost-cache");
    for sub in ["", "home", "runtime"] {
        let _ = std::fs::create_dir_all(cache_root.join(sub));
    }

    // Atomic write: a unit is replaced whole (systemd never reads a half of
    // it) and the destination name is never followed as a symlink.
    let unit_path = unit_dir.join(format!("{service_name}.service"));
    crate::atomic::write_atomic(&unit_path, content.as_bytes())
        .with_context(|| format!("writing unit {}", unit_path.display()))?;

    // Best effort reload/enable; failures surface at start time.
    let _ = systemctl(&["daemon-reload"]);
    let _ = systemctl(&["enable", &service_name]);
    Ok(())
}

/// Invoker-home subpaths a compromised service must never be able to read:
/// ssh keys, token stores, and the panel's own state/config. These never
/// overlap a per-service working dir, so blanking them is safe; we keep
/// `ProtectHome=read-only` instead of `ProtectHome=tmpfs` because the units
/// execute binaries that live under $HOME (panel, built artifacts) and a
/// tmpfs home would hide those too.
fn inaccessible_paths() -> String {
    let home = crate::paths::home();
    [
        ".ssh",
        ".config",
        ".gnupg",
        ".netrc",
        ".aws",
        ".cache",
        ".local/state/demo-ghostprovider",
    ]
    .into_iter()
    .map(|p| home.join(p))
    // systemd refuses to set up the namespace if a listed path does not
    // exist, so only the roots actually present are blanked.
    .filter(|p| p.exists())
    .map(|p| escape_unit_value(&p.to_string_lossy()))
    .collect::<Vec<_>>()
    .join(" ")
}

fn render_unit(spec: &UnitSpec) -> anyhow::Result<String> {
    let service_name = sanitize_service_name(spec.service_name);

    let mut env_lines = String::new();
    for (k, v) in spec.extra_env {
        let safe_k: String = k
            .chars()
            .filter(|c| c.is_ascii_alphanumeric() || *c == '_')
            .collect();
        env_lines.push_str(&format!(
            "Environment=\"{safe_k}={}\" \n",
            escape_unit_value(v)
        ));
    }

    let env_file_line = match spec.env_file {
        Some(p) if p.is_file() => format!(
            "EnvironmentFile={}\n",
            escape_unit_value(&p.canonicalize()?.to_string_lossy())
        ),
        _ => String::new(),
    };

    let mut res_lines = String::new();
    let limits = spec.res;
    if limits.memory_high.is_some()
        || limits.memory_max.is_some()
        || limits.tasks_max.is_some()
        || limits.cpu_quota.is_some()
        || limits.limit_nofile.is_some()
        || limits.oom_score_adjust.is_some()
    {
        res_lines.push_str("# -- Resource Limits --\n");
    }
    if let Some(h) = limits.memory_high {
        res_lines.push_str(&format!("MemoryHigh={h}\n"));
    }
    if let Some(m) = limits.memory_max {
        res_lines.push_str(&format!("MemoryMax={m}\n"));
    }
    if let Some(t) = limits.tasks_max {
        res_lines.push_str(&format!("TasksMax={t}\n"));
    }
    if let Some(c) = limits.cpu_quota {
        res_lines.push_str(&format!("CPUQuota={c}\n"));
    }
    if let Some(n) = limits.limit_nofile {
        res_lines.push_str(&format!("LimitNOFILE={n}\n"));
    }
    if let Some(o) = limits.oom_score_adjust {
        res_lines.push_str(&format!("OOMScoreAdjust={o}\n"));
    }

    // ProtectSystem=full keeps /usr and /boot read-only while leaving /etc
    // readable: strict mode breaks DNS resolution via /etc/resolv.conf.
    // Paths with spaces need quoting in list/command directives (verified
    // against systemd 261 via `systemd-analyze verify`); WorkingDirectory is
    // a single value and stays unquoted.
    let working = escape_unit_value(&spec.working_dir.to_string_lossy());
    let inaccessible = inaccessible_paths();
    let ip_allow = if spec.loopback_only {
        "IPAddressAllow=127.0.0.1 ::1\n"
    } else {
        ""
    };
    let content = format!(
        "[Unit]\nDescription={desc}\nAfter=network.target\n\n\
          [Service]\nType=simple\nWorkingDirectory={working}\nExecStart={exec}\n\
          Restart=always\nRestartSec=5\n{env_lines}{env_file_line}\n\
# A service must never inherit ambient CI credentials (GITHUB_TOKEN,
              # GH_TOKEN, package-manager tokens, openchamber/opencode session
              # secrets) or ambient session endpoints (SSH agent, D-Bus, X11)
              # via the manager environment. systemd rejects globs here, so
              # the known names are enumerated explicitly.\n\
         UnsetEnvironment=GITHUB_TOKEN GH_TOKEN NPM_TOKEN NODE_AUTH_TOKEN DOCKER_AUTH_CONFIG BUN_AUTH_TOKEN OPENCHAMBER_AGENT_TOOL_TOKEN OPENCHAMBER_TOKEN OPENCHAMBER_SESSION_ID OPENCODE_SERVER_PASSWORD OPENCODE_TOKEN OPENCODE_AUTH_TOKEN SSH_AUTH_SOCK SSH_ASKPASS GPG_AGENT_INFO DBUS_SESSION_BUS_ADDRESS DISPLAY WAYLAND_DISPLAY XAUTHORITY XDG_RUNTIME_DIR\n\
         # -- Privacy & Security Hardening --\n\
         NoNewPrivileges=yes\nProtectHome=read-only\nProtectSystem=full\n\
         ReadWritePaths=\"{working}\"\nEnvironment=\"HOME={working}/.ghost-cache/home\"\nEnvironment=\"XDG_CACHE_HOME={working}/.ghost-cache\"\nEnvironment=\"XDG_RUNTIME_DIR={working}/.ghost-cache/runtime\"\n\
         InaccessiblePaths={inaccessible}\n\
         ProtectKernelTunables=yes\nProtectKernelModules=yes\nProtectControlGroups=yes\n\
         ProtectClock=yes\nProtectHostname=yes\nProtectKernelLogs=yes\nPrivateIPC=yes\n\
         RestrictNamespaces=yes\nLockPersonality=yes\nRestrictRealtime=yes\n\
RestrictSUIDSGID=yes\nProtectProc=invisible\nPrivateDevices=yes\nProcSubset=pid\nRestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\nCapabilityBoundingSet=\nUMask=0077\n\
          SystemCallFilter=~@mount @swap @reboot @cpu-emulation @obsolete @module @raw-io @clock\n\
          {res_lines}{ip_allow}[Install]\nWantedBy=default.target\n",
        desc = escape_unit_value(if spec.description.is_empty() {
            &service_name
        } else {
            spec.description
        }),
        working = working,
        exec = quote_exec_args(spec.exec_start),
        res_lines = res_lines,
    );
    Ok(content)
}

#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum StartOutcome {
    Active,
    Failed,
    TimeoutWhileActivating,
    SystemdUnavailable,
}

fn query_state(service: &str) -> Option<String> {
    systemctl(&["is-active", service]).map(|(_, s)| s)
}

/// Poll until the service becomes active, fails, or the budget expires.
///
/// Unlike the Python original this never reports a crash merely because the
/// service was still `activating`.
pub fn wait_until_active(service: &str) -> StartOutcome {
    let deadline = Instant::now() + START_BUDGET;
    loop {
        match query_state(service).as_deref() {
            None => return StartOutcome::SystemdUnavailable,
            Some("active") => return StartOutcome::Active,
            Some("failed" | "inactive") => return StartOutcome::Failed,
            Some(_) => {} // activating / reloading / unknown-transient
        }
        if Instant::now() >= deadline {
            return StartOutcome::TimeoutWhileActivating;
        }
        std::thread::sleep(POLL_INTERVAL);
    }
}

/// Recent journal lines for a user unit.
pub fn service_logs(service: &str, lines: usize) -> String {
    Command::new("journalctl")
        .arg("--user")
        .arg("-u")
        .arg(service)
        .arg("-n")
        .arg(lines.to_string())
        .arg("--no-pager")
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_default()
}

/// Stop, disable and delete a user unit (best-effort cleanup).
pub fn remove_unit(service: &str) {
    let _ = systemctl(&["stop", service]);
    let _ = systemctl(&["disable", service]);
    let unit = crate::paths::user_unit_dir().join(format!("{service}.service"));
    let _ = std::fs::remove_file(unit);
    let _ = systemctl(&["daemon-reload"]);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unit_name_validation() {
        assert!(validate_unit_name(
            "demo-vert.service".trim_end_matches(".service")
        ));
        assert!(!validate_unit_name(""));
        assert!(!validate_unit_name("../evil"));
        assert!(!validate_unit_name("a b"));
        assert!(!validate_unit_name(&"x".repeat(256)));
    }

    #[test]
    fn sanitizes_hostile_names() {
        assert_eq!(
            sanitize_service_name("../../etc/passwd"),
            "../../etc/passwd".replace('/', "-")
        );
        assert_eq!(sanitize_service_name("demo vert"), "demo-vert");
    }

    /// Unit-file context escaping differs from EnvironmentFile context.
    #[test]
    fn unit_value_escaping() {
        assert_eq!(escape_unit_value("a$b"), "a$$b");
        assert_eq!(escape_unit_value("100%"), "100%%");
        assert_eq!(escape_unit_value("q\"q"), "q\\\"q");
        assert_eq!(escape_unit_value("l1\nl2"), "l1\\nl2");
    }

    /// Every ExecStart word is double-quoted so paths/spaces round-trip.
    #[test]
    fn exec_start_words_are_quoted() {
        let plain = quote_exec_args("/home/u bin/serve --port 8000");
        assert_eq!(plain, "\"/home/u\" \"bin/serve\" \"--port\" \"8000\"");
        let speced = quote_exec_args("/x/%h-demo --flag");
        assert_eq!(speced, "\"/x/%%h-demo\" \"--flag\"");
    }

    /// Resource caps from the recipe are rendered as real directives, and a
    /// `none()` spec emits no resource section at all.
    #[test]
    fn resource_limits_are_rendered_when_opted_in() {
        let spec = UnitSpec {
            service_name: "demo-vert",
            working_dir: Path::new("/tmp/vert"),
            exec_start: "/x/serve 8000",
            description: "demo",
            env_file: None,
            extra_env: &[],
            loopback_only: true,
            res: ResourceLimits {
                memory_high: Some("256M"),
                memory_max: Some("384M"),
                tasks_max: Some("300"),
                cpu_quota: Some("100%"),
                limit_nofile: Some(4096),
                oom_score_adjust: Some(-100),
            },
        };
        let content = render_unit(&spec).unwrap();
        for needle in [
            "# -- Resource Limits --\n",
            "MemoryHigh=256M\n",
            "MemoryMax=384M\n",
            "TasksMax=300\n",
            "CPUQuota=100%\n",
            "LimitNOFILE=4096\n",
            "OOMScoreAdjust=-100\n",
            // Hardening regression guards: HOME redirect + InaccessiblePaths
            // on the user's secret roots (tmpfs was rejected: unit binaries
            // live under $HOME, so a tmpfs home would hide them too), and the
            // seccomp deny-list. If these regress, a compromised service could
            // read the invoking user's ~/.ssh/tokens again.
            "ProtectHome=read-only\n",
            "Environment=\"HOME=/tmp/vert/.ghost-cache/home\"\n",
            "PrivateDevices=yes\n",
            "ProcSubset=pid\n",
            "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\n",
            "SystemCallFilter=~@mount @swap @reboot @cpu-emulation @obsolete @module @raw-io @clock\n",
        ] {
            assert!(content.contains(needle), "missing {needle:?}");
        }
// InaccessiblePaths is derived from $HOME (CI runs as /home/runner) and
// only includes roots that actually exist, so assert the directive is
// present and every existing secret root made it in.
let ins = content
            .lines()
            .find(|l| l.starts_with("InaccessiblePaths="))
            .expect("InaccessiblePaths directive must be present");
for candidate in [".ssh", ".config", ".local/state/demo-ghostprovider"] {
    let p = crate::paths::home().join(candidate);
    if !p.exists() {
        continue;
    }
    assert!(
        ins.contains(p.to_string_lossy().as_ref()),
        "missing {} in {}",
        p.display(),
        ins
    );
}        
        assert!(!content.contains("ProtectHome=tmpfs"));

        let bare = UnitSpec {
            res: ResourceLimits::none(),
            ..spec
        };
        let content = render_unit(&bare).unwrap();
        assert!(!content.contains("MemoryHigh="));
        assert!(!content.contains("MemoryMax="));
        assert!(!content.contains("TasksMax="));
        assert!(!content.contains("CPUQuota="));
        assert!(!content.contains("OOMScoreAdjust="));
    }
}
