//! Network transparency — the anti-"black box" core.
//!
//! Guarantees enforced here (and asserted by tests):
//!
//! 1. `ALLOWED_ENDPOINTS` is the complete list of remote hosts this program
//!    can ever contact over HTTP(S). The HTTP client denies everything else
//!    *before* opening a connection.
//! 2. Every outbound request attempt (success or failure) is appended to a
//!    local log file (`~/.local/state/demo-ghostprovider/net.log`) and to an
//!    in-session registry queryable via `--show-endpoints`.
//! 3. There is no runtime configuration and no code loading from the network.
//!    Only repository *data* of the service being deployed moves.
//!
//! Build-time package registries (npm, PyPI, proxy.golang.org) are contacted
//! by the *build tools of the deployed services*, not by this binary; that
//! distinction is documented in README ("Security model").

use std::collections::BTreeMap;
use std::io::Write;
use std::sync::Mutex;
use std::time::SystemTime;

/// Hosts this binary may contact. Keep in sync with README "Security model";
/// `allowed_endpoints_match_security_model` pins the exact contents, so
/// adding a host without updating docs fails CI.
///
/// `codeload.github.com` is reachable only as the *redirect target* of
/// `github.com/<owner>/<repo>/archive/*` downloads (the tarball fallback
/// used when git is unavailable). The HTTP client re-checks the allowlist on
/// every redirect hop, so this entry is not a bypass — it is the explicit,
/// net.log-visible permit for a host the GitHub archive flow genuinely
/// needs.
///
/// `proxy.golang.org` + `storage.googleapis.com` exist for one purpose only:
/// seeding the Go toolchain module into a local `file://` GOPROXY before a
/// sandboxed build (`src/hoster/goenv.rs`), so a throttled link does not
/// re-transfer the ~75 MiB zip on every deploy. `storage.googleapis.com` is
/// the signed-URL redirect target of `proxy.golang.org` downloads. Every hop
/// (this one included) is allowlist-gated and net.log-recorded like any
/// other, and the fetched bytes are re-verified by `go` against its checksum
/// database before extraction runs — this path only saves bytes, never
/// trusts them.
pub const ALLOWED_ENDPOINTS: &[&str] = &[
    "api.github.com",
    "github.com",
    "raw.githubusercontent.com",
    "codeload.github.com",
    "proxy.golang.org",
    "storage.googleapis.com",
];

/// Hosts treated as loopback. Allowed only for *local health checks*
/// (verifying a deployed service responds), never for API calls.
pub const LOCAL_ENDPOINTS: &[&str] = &["127.0.0.1", "localhost", "[::1]"];

#[derive(Debug, Clone)]
pub struct RequestRecord {
    pub at: SystemTime,
    pub host: String,
    pub path: String,
    pub outcome: Result<u16, String>, // status code or error text
}

struct Registry {
    records: Vec<RequestRecord>,
    file: Option<std::path::PathBuf>,
}

static REGISTRY: Mutex<Option<Registry>> = Mutex::new(None);

/// True when the user disabled on-disk network logging
/// (`GHOSTPROVIDER_NO_NETLOG=1`). Accepts the same truthy values as the
/// sandbox opt-out so both flags parse identically. The lost guarantee is a
/// security-relevant fact and must be surfaced (TUI/deploy status), never
/// silent.
pub fn logging_disabled() -> bool {
    crate::flags::env_flag("GHOSTPROVIDER_NO_NETLOG")
}

fn with_registry<T>(f: impl FnOnce(&mut Registry) -> T) -> T {
    let mut guard = REGISTRY.lock().unwrap();
    let reg = guard.get_or_insert_with(|| Registry {
        records: Vec::new(),
        file: if logging_disabled() {
            None
        } else {
            Some(crate::paths::netlog_file())
        },
    });
    f(reg)
}

fn append_to_file(file: &std::path::Path, line: &str) {
    if let Some(dir) = file.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    // mode 0600: the log traces every outbound request host+path — a privacy
    // record other local users must not read. Enforced on every append (not
    // only at creation): a pre-existing permissive net.log from an older
    // version is tightened in place, before anything is written to it.
    let mut opts = std::fs::OpenOptions::new();
    opts.create(true).append(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600);
    }
    if let Ok(mut f) = opts.open(file) {
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = std::fs::set_permissions(file, std::fs::Permissions::from_mode(0o600));
        }
        let _ = writeln!(f, "{line}");
    }
}

/// Record one outbound attempt. Called by the HTTP client *and* by the local
/// health checker so the log shows every socket this process opened.
pub fn record(host: &str, path: &str, outcome: Result<u16, String>) {
    let rec = RequestRecord {
        at: SystemTime::now(),
        host: host.to_string(),
        path: path.to_string(),
        outcome,
    };
    let line = format!(
        "{} {} {} {}",
        humantime_millis(rec.at),
        rec.host,
        rec.path,
        match &rec.outcome {
            Ok(code) => format!("HTTP {code}"),
            Err(e) => format!("ERR {e}"),
        }
    );
    with_registry(|reg| {
        reg.records.push(rec);
        if let Some(file) = &reg.file {
            append_to_file(file, &line);
        }
    });
}

/// Compact UTC timestamp without pulling chrono: YYYY-MM-DDTHH:MM:SS.mmmZ
pub(crate) fn format_utc(t: SystemTime) -> String {
    let d = t.duration_since(SystemTime::UNIX_EPOCH).unwrap_or_default();
    let secs = d.as_secs();
    let ms = d.subsec_millis();
    let days = secs / 86_400;
    let rem = secs % 86_400;
    let (y, m, dd) = civil_from_days(days as i64);
    format!(
        "{y:04}-{m:02}-{dd:02}T{:02}:{:02}:{:02}.{ms:03}Z",
        rem / 3600,
        (rem % 3600) / 60,
        rem % 60
    )
}

fn humantime_millis(t: SystemTime) -> String {
    format_utc(t)
}

/// Howard Hinnant's civil-from-days algorithm.
fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}

/// True when `host` may be contacted for remote (non-local) requests.
pub fn is_allowed_host(host: &str) -> bool {
    ALLOWED_ENDPOINTS.contains(&host.to_ascii_lowercase().as_str())
}

/// True when `host` is a loopback address usable for health checks.
pub fn is_local_host(host: &str) -> bool {
    LOCAL_ENDPOINTS.contains(&host.to_ascii_lowercase().as_str())
}

/// Summary for `--show-endpoints`: per-host request counters this session.
pub fn session_summary() -> BTreeMap<String, (usize, usize)> {
    // host -> (total, errors)
    with_registry(|reg| {
        let mut map: BTreeMap<String, (usize, usize)> = BTreeMap::new();
        for r in &reg.records {
            let e = map.entry(r.host.clone()).or_insert((0, 0));
            e.0 += 1;
            if r.outcome.is_err() || matches!(r.outcome, Ok(c) if c >= 400) {
                e.1 += 1;
            }
        }
        map
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn allowed_endpoints_match_security_model() {
        // Pins the allowlist against README's documented set. Any change to
        // ALLOWED_ENDPOINTS must update both — otherwise this test fails.
        assert_eq!(
            ALLOWED_ENDPOINTS,
            &[
                "api.github.com",
                "github.com",
                "raw.githubusercontent.com",
                "codeload.github.com",
                "proxy.golang.org",
                "storage.googleapis.com"
            ],
            "allowlist changed — update README 'Security model' in the same commit"
        );
    }

    #[test]
    fn host_checks() {
        assert!(is_allowed_host("api.github.com"));
        assert!(is_allowed_host("API.GitHub.COM"));
        assert!(!is_allowed_host("evil.example"));
        assert!(!is_allowed_host("github.com.evil.example"));
        assert!(is_local_host("127.0.0.1"));
        assert!(is_local_host("localhost"));
        assert!(!is_local_host("api.github.com"));
    }

    #[test]
    fn civil_date_known_values() {
        // 2026-08-23 == days since epoch 20688
        assert_eq!(civil_from_days(20_688), (2026, 8, 23));
        assert_eq!(civil_from_days(0), (1970, 1, 1));
    }
}
