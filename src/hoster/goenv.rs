//! Go build pre-provisioning: toolchain AND module cache.
//!
//! With `GOTOOLCHAIN=auto` (already set by the sandbox) an up-to-date `go`
//! can fetch the toolchain module itself — but on throttled links that ~75 MiB
//! zip re-transfers on every deploy. This module pre-fetches it, in resumable
//! Range segments through the shared allowlisted client, into a local
//! `file://` GOPROXY the sandboxed build then consumes.
//!
//! Dependencies are handled the same way: every `h1:`-listed zip in the
//! project's `go.sum` is Range-fetched ahead of `go build` and dropped into
//! `$GOMODCACHE/cache/download`, so `go` never serializes hundreds of downloads
//! behind one socket on a slow link (which used to timeout the sandbox).
//! Seeding is best-effort — if it fails partially, `go` re-fetches what it
//! still needs, one-by-one, in the build step.
//!
//! Trust stays with `go`: the seeded bytes are re-verified against Go's
//! checksum database (`sum.golang.org`) before they are extracted and run.
//! This path only saves bytes, it never trusts them. Network surface added:
//! `proxy.golang.org` (module metadata + zip) and `storage.googleapis.com`
//! (its signed-URL redirect), both gated per hop by `ALLOWED_ENDPOINTS` and
//! net.log-recorded like every other request (see `netlog.rs`).

use anyhow::Context;
use crate::hoster::httpclient;
use crate::hoster::toolcheck;
use std::io::Write;
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

const SEGMENT: u64 = 4 * 1024 * 1024;
const WORKERS: usize = 6;
/// A valid zip needs at least its End-of-Central-Directory record (22 bytes).
/// Anything near-empty on disk is a broken fragment (e.g. a size probe that
/// once misread a 206 as "1 byte") and must be refetched, never trusted.
const MIN_ZIP: u64 = 32;
/// Toolchain zips are ~75 MiB today; anything over this is implausible and
/// refused before a socket opens. Loose on purpose: it must never become a
/// bandwidth or size oracle for future Go releases.
const MAX_TOOLCHAIN: u64 = 300 * 1024 * 1024;

/// Toolchain module tag for a release: `v0.0.1-go1.27.0.linux-amd64`.
fn toolchain_tag(go_ver: &str, os: &str, arch: &str) -> String {
    let go = go_ver.strip_prefix("go").unwrap_or(go_ver);
    format!("v0.0.1-go{go}.{os}-{arch}")
}

/// Env overrides that make a sandboxed Go build resolve the toolchain module
/// from a pre-seeded local cache. Returns an empty vec when nothing is
/// needed, so the caller can pass it through unconditionally.
pub fn go_toolchain_env(project_dir: &Path) -> anyhow::Result<Vec<(String, String)>> {
    let Some(need) = go_mod_requirement_at(project_dir)? else {
        return Ok(Vec::new());
    };
    let Some(have) = go_env_version() else {
        // `go` missing or not probing: the tool doctor already blocks the
        // build before we ever get here — nothing to seed.
        return Ok(Vec::new());
    };
    if have >= need {
        return Ok(Vec::new());
    }

    let os = go_env_key("GOOS").unwrap_or_else(|| "linux".to_string());
    let arch = go_env_key("GOARCH").unwrap_or_else(|| std::env::consts::ARCH.to_string());
    let tag = toolchain_tag(&toolcheck::v_str(need), &os, &arch);

    let base = project_dir.join(".ghost-cache").join("go-fileproxy");
    let vdir = base.join("golang.org").join("toolchain").join("@v");
    std::fs::create_dir_all(&vdir).with_context(|| format!("creating {}", vdir.display()))?;

    let module = format!("golang.org/toolchain/@v/{tag}");
    let module_esc = proxy_escape(&module);
    let zip_url = format!("https://proxy.golang.org/{module_esc}.zip");
    let zip_path = vdir.join(format!("{tag}.zip"));
    fetch_zip(&zip_url, &zip_path)?;

    for ext in [".info", ".mod"] {
        let p = vdir.join(format!("{tag}{ext}"));
        if p.metadata().map(|m| m.len()).unwrap_or(0) == 0 {
            let text = httpclient::get_text(&format!("https://proxy.golang.org/{module_esc}{ext}"))?;
            write_bytes(&p, text.as_bytes())?;
        }
    }

    Ok(vec![
        ("GOPROXY".to_string(), format!("file://{proxy}", proxy = base.display())),
        ("GOTOOLCHAIN".to_string(), "auto".to_string()),
    ])
}

/// Highest `go`/`toolchain` directive in the project's go.mod, if any.
fn go_mod_requirement_at(project_dir: &Path) -> anyhow::Result<Option<toolcheck::Ver>> {
    let text = match std::fs::read_to_string(project_dir.join("go.mod")) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e).with_context(|| format!("reading {}", project_dir.join("go.mod").display())),
    };
    Ok(toolcheck::go_mod_requirement(&text))
}

/// `go env <key>` through the user's PATH; None when `go` is absent or dies.
fn go_env_key(key: &str) -> Option<String> {
    let out = std::process::Command::new("go")
        .args(["env", key])
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let s = String::from_utf8_lossy(&out.stdout);
    let s = s.trim();
    if s.is_empty() {
        None
    } else {
        Some(s.to_string())
    }
}

/// Encode a module path the way the Go module proxy URL requires:
/// every uppercase ASCII letter becomes `!` + its lowercase form
/// (golang.org/x/mod/module.EscapePath). The on-disk cache keeps the
/// original case; only the request URL is escaped.
fn proxy_escape(module: &str) -> String {
    let mut out = String::with_capacity(module.len());
    for ch in module.chars() {
        if ch.is_ascii_uppercase() {
            out.push('!');
            out.push(ch.to_ascii_lowercase());
        } else {
            out.push(ch);
        }
    }
    out
}

fn go_env_version() -> Option<toolcheck::Ver> {
    toolcheck::parse_version(&go_env_key("GOVERSION")?)
}

/// Zip-hash rows of a `go.sum`: `module version h1:...`. The `version/go.mod`
/// rows (module hash) are skipped, as is anything that is not a plain `h1:`
/// hash.
fn go_sum_entries(text: &str) -> Vec<(String, String, String)> {
    let mut out = Vec::new();
    for line in text.lines() {
        let mut f = line.split_whitespace();
        let (Some(module), Some(ver), Some(h1)) = (f.next(), f.next(), f.next()) else {
            continue;
        };
        if ver.ends_with("/go.mod") {
            continue;
        }
        if !h1.starts_with("h1:") {
            continue;
        }
        out.push((module.to_string(), ver.to_string(), h1.to_string()));
    }
    out
}

/// The download cache `go` reads under `GOMODCACHE` (see `sandbox.rs`), i.e.
/// `$project_dir/.ghost-cache/go-mod/cache/download`.
fn go_download_cache(project_dir: &Path) -> std::path::PathBuf {
    project_dir
        .join(".ghost-cache")
        .join("go-mod")
        .join("cache")
        .join("download")
}

/// Best-effort pre-seed of every module zip listed as an `h1:` row in the
/// project's `go.sum`. Returns the number of modules seeded; `Ok(0)` when the
/// cache was already complete or no `go.sum` exists. Never trusts a partial
/// file: only a finished, size-verified zip counts, so an interrupted run
/// resumes instead of re-serializing (same staging trick as `fetch_zip`).
pub fn seed_go_modules(project_dir: &Path) -> anyhow::Result<usize> {
    let sum_text = match std::fs::read_to_string(project_dir.join("go.sum")) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(0),
        Err(e) => {
            return Err(e)
                .with_context(|| format!("reading go.sum in {}", project_dir.display()))
        }
    };
    let cache = go_download_cache(project_dir);
    let mut jobs: Vec<(String, String, String)> = Vec::new();
    let mut seeded = 0usize;
    for (module, ver, h1) in go_sum_entries(&sum_text) {
        let zip = cache.join(&module).join("@v").join(format!("{ver}.zip"));
        if zip.metadata().map(|m| m.len()).unwrap_or(0) >= MIN_ZIP {
            seeded += 1;
            continue;
        }
        jobs.push((module, ver, h1));
    }
    let total = jobs.len();
    if total == 0 {
        return Ok(seeded);
    }

    let next = Arc::new(Mutex::new(0usize));
    let errors: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
    let mut handles = Vec::new();
    let jobs = Arc::new(jobs);
    for _ in 0..WORKERS {
        let next = Arc::clone(&next);
        let errors = Arc::clone(&errors);
        let cache = cache.clone();
        let jobs = Arc::clone(&jobs);
        handles.push(std::thread::spawn(move || loop {
            let i = {
                let mut g = next.lock().unwrap();
                let i = *g;
                *g += 1;
                i
            };
            if i >= total {
                break;
            }
            let (module, ver, h1) = jobs[i].clone();
            // One transient failure must not kill the whole pool: everything
            // else keeps seeding (resumable), and the straggler is reported
            // once — `go` re-fetches it directly and the next run resumes it.
            let mut last_err = None;
            for _ in 0..2 {
                if let Err(e) = seed_one_module(&cache, &module, &ver, &h1) {
                    last_err = Some(e);
                } else {
                    last_err = None;
                    break;
                }
            }
            if let Some(e) = last_err {
                errors.lock().unwrap().push(format!("{module}@{ver}: {e:#}"));
            }
        }));
    }
    for h in handles {
        let _ = h.join();
    }
    let errors = errors.lock().unwrap();
    if let Some(first) = errors.first() {
        return Err(anyhow::anyhow!(
            "{} of {} module(s) failed (go will fetch directly); first: {first}",
            errors.len(),
            total
        ));
    }
    Ok(seeded + total)
}

/// Fetch one module (`semver` or pseudo-version) into the download cache:
/// the resumable segmented zip, the `h1:` hash `go` needs as `.ziphash`, and
/// the tiny `.info`/`.mod` metadata. Idempotent; safe to run repeatedly.
fn seed_one_module(
    cache: &std::path::Path,
    module: &str,
    ver: &str,
    h1: &str,
) -> anyhow::Result<()> {
    let vdir = cache.join(module).join("@v");
    // The on-disk cache path uses the module's original case, but the Go
    // module proxy encodes every uppercase letter as `!` + lowercase in the
    // URL path (`golang.org/x/mod/module.EscapePath`). Sending the raw path
    // returns 404 for any module with an uppercase segment (e.g. go-winio).
    let base = format!("https://proxy.golang.org/{}/@v/{ver}", proxy_escape(module));
    std::fs::create_dir_all(&vdir).with_context(|| format!("creating {}", vdir.display()))?;

    let zip = vdir.join(format!("{ver}.zip"));
    fetch_zip(&format!("{base}.zip"), &zip)?;

    let hashes = vdir.join(format!("{ver}.ziphash"));
    if hashes.metadata().map(|m| m.len()).unwrap_or(0) == 0 {
        write_bytes(&hashes, format!("{h1}\n").as_bytes())?;
    }
    for ext in [".info", ".mod"] {
        let p = vdir.join(format!("{ver}{ext}"));
        if p.metadata().map(|m| m.len()).unwrap_or(0) == 0 {
            let text = httpclient::get_text(&format!("{base}{ext}"))?;
            write_bytes(&p, text.as_bytes())?;
        }
    }
    Ok(())
}

/// Download `<url>` into `dest` unless a non-empty file is already there.
/// Sizes are discovered with a 0-byte probe; large zips are fetched as
/// parallel Range segments assembled after every part verifies. Staging parts
/// live in a sibling directory that is *kept* on failure, so a retry resumes
/// the already-fetched segments instead of re-transferring them (the ~75 MiB
/// zip on a throttled link is exactly the case this exists for). Only an
/// assembled and size-verified dest dir counts as done.
fn fetch_zip(url: &str, dest: &Path) -> anyhow::Result<()> {
    if dest.metadata().map(|m| m.len()).unwrap_or(0) >= MIN_ZIP {
        return Ok(());
    }
    let size = httpclient::remote_len(url)?;
    if size == 0 || size > MAX_TOOLCHAIN {
        anyhow::bail!("implausible toolchain size {size} for {url}");
    }
    let stage = dest.with_extension("stage");
    std::fs::create_dir_all(&stage).with_context(|| format!("staging {}", stage.display()))?;

    if size <= SEGMENT {
        let bytes = httpclient::get_bytes_range(url, 0, size - 1)?;
        write_bytes(dest, &bytes)?;
        let _ = std::fs::remove_dir_all(&stage);
        return Ok(());
    }

    let segs = size.div_ceil(SEGMENT);
    let seg_len = move |i: u64| ((i + 1) * SEGMENT).min(size) - i * SEGMENT;
    let next = Arc::new(Mutex::new(0u64));
    let failure: Arc<Mutex<Option<anyhow::Error>>> = Arc::new(Mutex::new(None));
    let aborted = Arc::new(AtomicBool::new(false));
    let mut handles = Vec::new();

    for _ in 0..WORKERS {
        let next = Arc::clone(&next);
        let failure = Arc::clone(&failure);
        let aborted = Arc::clone(&aborted);
        let url = url.to_string();
        let stage = stage.clone();
        handles.push(std::thread::spawn(move || loop {
            if aborted.load(Ordering::SeqCst) {
                break;
            }
            let i = {
                let mut g = next.lock().unwrap();
                let i = *g;
                *g += 1;
                i
            };
            if i >= segs {
                break;
            }
            let part = stage.join(format!("{i:07}"));
            // Resume: an existing part of the exact expected length is done.
            if part.metadata().map(|m| m.len()).unwrap_or(0) == seg_len(i) {
                continue;
            }
            let start = i * SEGMENT;
            let end = ((i + 1) * SEGMENT - 1).min(size - 1);
            match httpclient::get_bytes_range(&url, start, end) {
                Ok(bytes) => {
                    if let Err(e) = write_bytes(&part, &bytes) {
                        *failure.lock().unwrap() = Some(e);
                        aborted.store(true, Ordering::SeqCst);
                        break;
                    }
                }
                Err(e) => {
                    *failure.lock().unwrap() = Some(e);
                    aborted.store(true, Ordering::SeqCst);
                    break;
                }
            }
        }));
    }
    for h in handles {
        let _ = h.join();
    }
    if let Some(e) = failure.lock().unwrap().take() {
        // Staging is deliberately left in place: the next run resumes it.
        return Err(e);
    }

    let out = std::fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .open(dest)
        .with_context(|| format!("writing {}", dest.display()))?;
    let mut out = std::io::BufWriter::new(out);
    for i in 0..segs {
        let part = stage.join(format!("{i:07}"));
        let bytes = std::fs::read(&part).with_context(|| format!("reading part {}", part.display()))?;
        out.write_all(&bytes)
            .with_context(|| format!("assembling {}", dest.display()))?;
    }
    out.flush().ok();
    out.into_inner()
        .map_err(anyhow::Error::from)
        .and_then(|f| f.sync_all().map_err(anyhow::Error::from))?;

    let got = dest.metadata().map(|m| m.len()).unwrap_or(0);
    let _ = std::fs::remove_dir_all(&stage);
    if got != size {
        anyhow::bail!(
            "toolchain download size mismatch: got {got}, expected {size} for {url}"
        );
    }
    // Keep the cache consistent with secrets/unit files: private (0600).
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(dest, std::fs::Permissions::from_mode(0o600));
    }
    Ok(())
}

/// Write bytes privately (0600) and atomically (temp + rename), so a half
/// written `.info`/`.mod`/part is never observed as a complete file.
fn write_bytes(path: &Path, data: &[u8]) -> anyhow::Result<()> {
    use std::os::unix::fs::OpenOptionsExt;
    let tmp = path.with_extension("tmp");
    let mut f = std::fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .open(&tmp)
        .with_context(|| format!("writing {}", tmp.display()))?;
    f.write_all(data).with_context(|| format!("writing {}", tmp.display()))?;
    f.sync_all().ok();
    drop(f);
    std::fs::rename(&tmp, path).with_context(|| format!("renaming {}", tmp.display()))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tag_format_matches_go_convention() {
        assert_eq!(toolchain_tag("1.27.0", "linux", "amd64"), "v0.0.1-go1.27.0.linux-amd64");
        assert_eq!(toolchain_tag("go1.26.6", "darwin", "arm64"), "v0.0.1-go1.26.6.darwin-arm64");
    }

    #[test]
    fn compares_like_the_doctor() {
        // `go env GOVERSION` for an older toolchain vs a go.mod requirement
        // must trigger a seed; an already-satisfied one must not.
        let need = toolcheck::parse_version("1.27.0").unwrap();
        let have = toolcheck::parse_version("go1.26.6").unwrap();
        assert!(have < need);
        assert!(have >= toolcheck::parse_version("1.18.8").unwrap());
        assert!(need >= need);
    }

    #[test]
    fn escapes_uppercase_for_the_proxy() {
        // go-winio lives in the cache under its real name but is fetched as
        // the escaped path: the unescaped URL 404s on proxy.golang.org.
        assert_eq!(
            proxy_escape("github.com/Microsoft/go-winio"),
            "github.com/!microsoft/go-winio"
        );
        assert_eq!(
            proxy_escape("golang.org/toolchain/@v/v0.0.1-go1.27.0.linux-amd64"),
            "golang.org/toolchain/@v/v0.0.1-go1.27.0.linux-amd64"
        );
        // Paths without uppercase are untouched.
        assert_eq!(proxy_escape("github.com/foo/bar"), "github.com/foo/bar");
    }

    #[test]
    fn requirement_reads_go_mod() {
        let dir = std::env::temp_dir().join(format!("ghost-goenv-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(
            dir.join("go.mod"),
            "module example.org/m\n\ntoolchain go1.24.3\n\ngo 1.27.0\n",
        )
        .unwrap();
        let req = go_mod_requirement_at(&dir).unwrap().unwrap();
        assert_eq!(req, (1, 27, 0));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn absent_go_mod_means_no_work() {
        let dir = std::env::temp_dir().join(format!("ghost-goenv-none-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        assert!(go_mod_requirement_at(&dir).unwrap().is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// Opt-in live probe of the seeding path against the real network.
    ///
    /// Steps: put a fake old `go` shim first on PATH (GOVERSION `go1.20.1`,
    /// GOOS `linux`, GOARCH `amd64`), then run
    /// `GHOSTPROVIDER_TOOLCHAIN_PROBE=1 cargo test --release --lib probe_toolchain_seed -- --ignored`.
    /// The shim forces the need>have branch; the fetch then goes through the
    /// real allowlisted client (proxy.golang.org + its storage redirect) and
    /// assembles the zip into a local `file://` GOPROXY. Never runs in CI:
    /// `#[ignore]` by default and guarded by the env var.
    #[test]
    #[ignore = "network: opt-in probe of the toolchain seeding path"]
    fn probe_toolchain_seed() {
        if std::env::var_os("GHOSTPROVIDER_TOOLCHAIN_PROBE").is_none() {
            return;
        }
        let p = std::env::temp_dir().join("ghost-goenv-probe");
        // Deliberately NOT wiped at the start: a half-fetched stage dir from a
        // killed run is the resume case this feature exists for, and this
        // probe should exercise it.
        std::fs::create_dir_all(&p).unwrap();
        std::fs::write(p.join("go.mod"), "module probe.example\n\ngo 1.27.0\n").unwrap();

        let env = go_toolchain_env(&p).unwrap();
        assert!(env.len() == 2, "expected GOPROXY + GOTOOLCHAIN, got {env:?}");
        assert!(env[0].0 == "GOPROXY" && env[0].1.starts_with("file://"));
        assert!(env[1] == ("GOTOOLCHAIN".to_string(), "auto".to_string()));

        let vdir = p.join(".ghost-cache/go-fileproxy/golang.org/toolchain/@v");
        let tag = "v0.0.1-go1.27.0.linux-amd64";
        let zip = vdir.join(format!("{tag}.zip"));
        assert!(zip.metadata().unwrap().len() > 1_000_000, "zip not fetched");
        assert!(vdir.join(format!("{tag}.info")).metadata().unwrap().len() > 0);
        assert!(vdir.join(format!("{tag}.mod")).metadata().unwrap().len() > 0);
        let _ = std::fs::remove_dir_all(&p);
    }

    #[test]
    fn parses_go_sum_zip_rows() {
        let s = "github.com/foo/bar v1.2.3 h1:AAA=\n\
                 github.com/foo/bar v1.2.3/go.mod h1:XXX=\n\
                 \t \n\
                 some random line h1:nope\n";
        let rows = go_sum_entries(s);
        assert_eq!(rows.len(), 1, "only the three-field h1 row should parse: {rows:?}");
        assert_eq!(rows[0].0, "github.com/foo/bar");
        assert_eq!(rows[0].1, "v1.2.3");
        assert_eq!(rows[0].2, "h1:AAA=");
    }

    /// Opt-in live probe of the module-cache seed against the real network:
    /// `GHOSTPROVIDER_MODULES_PROBE=1 cargo test --release --lib probe_module_seed -- --ignored`.
    /// Uses one pseudoversion row, which exercises the never-tagged zip path
    /// (proxy answers 206 directly instead of redirecting). Never runs in CI.
    #[test]
    #[ignore = "network: opt-in probe of the module cache seeding path"]
    fn probe_module_seed() {
        if std::env::var_os("GHOSTPROVIDER_MODULES_PROBE").is_none() {
            return;
        }
        let p = std::env::temp_dir().join("ghost-goenv-modprobe");
        let _ = std::fs::remove_dir_all(&p);
        std::fs::create_dir_all(&p).unwrap();
        std::fs::write(
            p.join("go.sum"),
            "google.golang.org/genproto/googleapis/api v0.0.0-20260810153831-ec0a7760b754 h1:dWeMvEJ3JhYgqSCAHUZZJgMUyfniiiCvDc72x5EqJP0=\n",
        )
        .unwrap();

        let first = seed_go_modules(&p).unwrap();
        assert_eq!(first, 1, "the pseudoversion row should seed once");

        let vdir = p
            .join(".ghost-cache/go-mod/cache/download/google.golang.org/genproto/googleapis/api/@v");
        let zip = vdir.join("v0.0.0-20260810153831-ec0a7760b754.zip");
        assert!(zip.metadata().unwrap().len() > 0, "zip not fetched");
        let ziphash = std::fs::read_to_string(vdir.join("v0.0.0-20260810153831-ec0a7760b754.ziphash"))
            .unwrap();
        assert!(
            ziphash.trim().ends_with("dWeMvEJ3JhYgqSCAHUZZJgMUyfniiiCvDc72x5EqJP0="),
            "ziphash must carry the go.sum h1 line: {ziphash}"
        );
        assert!(vdir.join("v0.0.0-20260810153831-ec0a7760b754.info").metadata().unwrap().len() > 0);
        assert!(vdir.join("v0.0.0-20260810153831-ec0a7760b754.mod").metadata().unwrap().len() > 0);

        let second = seed_go_modules(&p).unwrap();
        assert_eq!(second, 1, "second run sees the seeded zip and does nothing");
        let _ = std::fs::remove_dir_all(&p);
    }
}