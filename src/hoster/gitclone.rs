//! Git clone with retries, backoff and tarball fallback.
//!
//! Credentials: `GITHUB_TOKEN`/`GH_TOKEN` (if set) is exposed to git via a
//! temporary GIT_ASKPASS helper so the token never appears in argv
//! (`/proc/PID/cmdline`) or shell history. The helper is 0600 in tmpfs-ish
//! temp dir and removed afterwards.

use std::collections::HashMap;
use std::path::Path;
use std::process::Command;

const CLONE_RETRIES: u32 = 3;

fn git_env(askpass: Option<&Path>) -> HashMap<String, String> {
    let mut env: HashMap<String, String> = std::env::vars().collect();
    // Stable, honest UA: identify the tool instead of spoofing a browser.
    env.insert("GIT_CONFIG_COUNT".into(), "1".into());
    env.insert("GIT_CONFIG_KEY_0".into(), "http.userAgent".into());
    env.insert(
        "GIT_CONFIG_VALUE_0".into(),
        format!("demo-ghostprovider/{}", env!("CARGO_PKG_VERSION")),
    );
    env.insert("GIT_TERMINAL_PROMPT".into(), "0".into());
    if let Some(p) = askpass {
        env.insert("GIT_ASKPASS".into(), p.to_string_lossy().into_owned());
    }
    env
}

fn write_askpass() -> anyhow::Result<Option<std::path::PathBuf>> {
    let token = std::env::var("GITHUB_TOKEN")
        .or_else(|_| std::env::var("GH_TOKEN"))
        .ok()
        .filter(|t| !t.is_empty());
    let Some(token) = token else { return Ok(None) };

    // Prefer XDG_RUNTIME_DIR (per-user, 0700, tmpfs) so other local users
    // cannot even enumerate the helper; fall back to /tmp. The file is
    // created with O_EXCL + 0600 — never write-then-chmod through a name an
    // attacker could have predicted and symlinked.
    let parent = std::env::var_os("XDG_RUNTIME_DIR")
        .map(std::path::PathBuf::from)
        .filter(|p| p.is_dir())
        .unwrap_or_else(std::env::temp_dir);
    let content = format!("#!/bin/sh\necho '{}'\n", shell_quote(&token));
    for _ in 0..100 {
        let path = parent.join(format!(
            "gp-askpass-{}-{}",
            std::process::id(),
            crate::atomic::random_hex(8).unwrap_or_default()
        ));
        #[cfg(unix)]
        {
            use std::io::Write;
            use std::os::unix::fs::OpenOptionsExt;
            match std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600)
                .open(&path)
            {
                Ok(mut f) => {
                    let _ = f.write_all(content.as_bytes());
                    return Ok(Some(path));
                }
                Err(_) => continue, // name collision: try another
            }
        }
        #[cfg(not(unix))]
        {
            match std::fs::write(&path, &content) {
                Ok(()) => return Ok(Some(path)),
                Err(_) => continue,
            }
        }
    }
    anyhow::bail!("could not create a unique askpass helper")
}

/// Single-quote for POSIX sh: `'` → `'\''`.
fn shell_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

/// `remove_dir_all` that survives read-only directories.
///
/// Go's module cache extracts archives with mode 0555 directories; deleting
/// entries inside a directory requires write permission on it, so plain
/// `fs::remove_dir_all` fails halfway through a project tree containing
/// `.ghost-cache/go-mod` and leaves an un-removable husk behind. Walk
/// bottom-up ourselves and add owner-write back before each rmdir.
pub(crate) fn force_remove_all(path: &Path) -> std::io::Result<()> {
    let meta = std::fs::symlink_metadata(path)?;
    if meta.is_dir() {
        // Writable-parent first: unlinking ANY entry below requires write
        // permission on this directory, so fix the mode before recursing.
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut perms = meta.permissions();
            if perms.mode() & 0o300 != 0o300 {
                perms.set_mode(perms.mode() | 0o300); // u+w, u+x
                std::fs::set_permissions(path, perms)?;
            }
        }
        for entry in std::fs::read_dir(path)? {
            force_remove_all(&entry?.path())?;
        }
        std::fs::remove_dir(path)
    } else {
        std::fs::remove_file(path)
    }
}

pub struct CloneStatus {
    pub ok: bool,
    pub last_message: String,
}

/// A reusable clone must be intact. An interrupted clone can leave `.git`
/// with a *partial* checkout: the index lists files that never made it to
/// disk, every later build fails far away from the real cause ("no required
/// module provides package …"), and `clone()` happily reports
/// "already cloned" forever. Deleted-but-tracked files ⇒ corrupt.
fn worktree_intact(dest: &Path) -> bool {
    // A real clone has a commit; a tarball-fallback dir only ever gets a
    // bare `git init`, which must not count as reusable.
    let committed = Command::new("git")
        .arg("-C")
        .arg(dest)
        .args(["rev-parse", "--verify", "HEAD"])
        .env_remove("GIT_ASKPASS")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false);
    if !committed {
        return false;
    }
    let Ok(out) = Command::new("git")
        .arg("-C")
        .arg(dest)
        .args(["ls-files", "--deleted"])
        .env_remove("GIT_ASKPASS")
        .output()
    else {
        return false;
    };
    out.status.success() && String::from_utf8_lossy(&out.stdout).trim().is_empty()
}

/// Clone `url` into `dest`. Returns immediately when the repo already exists.
pub fn clone(url: &str, dest: &Path) -> CloneStatus {
    if dest.join(".git").is_dir() {
        if worktree_intact(dest) {
            return CloneStatus {
                ok: true,
                last_message: "already cloned".into(),
            };
        }
        // Broken checkout: start over instead of failing the build later.
        eprintln!("clone: existing copy is incomplete — recloning");
        let _ = force_remove_all(dest);
    }
    let _ = force_remove_all(dest);

    let askpass = match write_askpass() {
        Ok(p) => p,
        Err(e) => {
            return CloneStatus {
                ok: false,
                last_message: format!("askpass setup failed: {e}"),
            };
        }
    };
    let env = git_env(askpass.as_deref());

    let strategies: [Vec<String>; 2] = [
        vec![
            "clone".into(),
            "--depth".into(),
            "1".into(),
            "--single-branch".into(),
            "--no-tags".into(),
            url.into(),
            dest.to_string_lossy().into_owned(),
        ],
        vec![
            "clone".into(),
            "--single-branch".into(),
            url.into(),
            dest.to_string_lossy().into_owned(),
        ],
    ];

    for attempt in 0..CLONE_RETRIES {
        let args = &strategies[(attempt % strategies.len() as u32) as usize];
        match run_git(args, &env) {
            Some(out) if out.status.success() && dest.join(".git").is_dir() => {
                cleanup_askpass(askpass.as_deref());
                return CloneStatus {
                    ok: true,
                    last_message: "clone complete".into(),
                };
            }
            Some(out) => {
                let err = String::from_utf8_lossy(&out.stderr).trim().to_string();
                let _ = force_remove_all(dest);
                if !err.is_empty() {
                    eprintln!("git error: {}", short(&err));
                }
            }
            None => {
                /* git missing — fall through to tarball */
                break;
            }
        }
        let wait = std::cmp::min(5 * (2u32.pow(attempt)), 60);
        std::thread::sleep(std::time::Duration::from_secs(wait as u64));
    }

    let status = tarball_fallback(url, dest);
    cleanup_askpass(askpass.as_deref());
    status
}

fn tarball_fallback(url: &str, dest: &Path) -> CloneStatus {
    let base = url.trim_end_matches(".git").trim_end_matches('/');
    let candidates = [
        format!("{base}/archive/refs/heads/master.tar.gz"),
        format!("{base}/archive/refs/heads/main.tar.gz"),
    ];
    for tb in candidates {
        // Fetch through the allowlisted client so the download is net.log-accounted
        // and the token stays out of argv (it only ever rides an Authorization
        // header for api.github.com — never for tarball hosts).
        let data = match super::httpclient::get_bytes(&tb) {
            Ok(bytes) if !bytes.is_empty() => bytes,
            _ => continue,
        };
        let tmp = match write_tarball_temp(&data) {
            Some(p) => p,
            None => continue,
        };
        let _ = std::fs::create_dir_all(dest);
        let untar = Command::new("tar")
            .args(["xzf"])
            .arg(&tmp)
            .arg("-C")
            .arg(dest)
            .arg("--strip-components=1")
            .output();
        let _ = std::fs::remove_file(&tmp);
        match untar {
            Ok(o) if o.status.success() => {
                let _ = Command::new("git").arg("init").current_dir(dest).output();
                return CloneStatus {
                    ok: true,
                    last_message: "tarball download complete".into(),
                };
            }
            Ok(_) => {
                let _ = force_remove_all(dest);
                eprintln!("tarball extract failed");
            }
            Err(_) => {}
        }
    }
    CloneStatus {
        ok: false,
        last_message: "all clone strategies failed".into(),
    }
}

/// Write downloaded bytes to a private, unique temp file, refusing to follow
/// a pre-existing symlink (O_EXCL) and never leaving readable leftovers.
fn write_tarball_temp(data: &[u8]) -> Option<std::path::PathBuf> {
    use std::io::Write;
    for _ in 0..100 {
        let path = std::env::temp_dir().join(format!(
            "gp-tarball-{}-{}",
            std::process::id(),
            crate::atomic::random_hex(8).unwrap_or_default()
        ));
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            if let Ok(mut f) = std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600)
                .open(&path)
            {
                let _ = f.write_all(data);
                return Some(path);
            }
        }
        #[cfg(not(unix))]
        {
            if std::fs::write(&path, data).is_ok() {
                return Some(path);
            }
        }
    }
    None
}

fn run_git(args: &[String], env: &HashMap<String, String>) -> Option<std::process::Output> {
    let mut cmd = Command::new("git");
    cmd.args(args).env_clear().envs(env);
    cmd.output().ok()
}

fn cleanup_askpass(path: Option<&std::path::Path>) {
    if let Some(p) = path {
        let _ = std::fs::remove_file(p);
    }
}

fn short(s: &str) -> String {
    s.chars().take(120).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn git(dir: &Path, args: &[&str]) -> bool {
        Command::new("git")
            .args(args)
            .current_dir(dir)
            .env("GIT_AUTHOR_NAME", "t")
            .env("GIT_AUTHOR_EMAIL", "t@t")
            .env("GIT_COMMITTER_NAME", "t")
            .env("GIT_COMMITTER_EMAIL", "t@t")
            .output()
            .map(|o| o.status.success())
            .unwrap_or(false)
    }

    /// Reproduces the corrupted-clone scenario: a tracked file that vanished
    /// from the working tree must mark the clone as not intact.
    #[test]
    fn deleted_tracked_file_breaks_intactness() {
        if !which_git() {
            return; // git is an install requirement, but don't fail exotic CI
        }
        let dir = std::env::temp_dir().join(format!("dgp-intact-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        assert!(git(&dir, &["init", "-q"]));
        std::fs::write(dir.join("go.mod"), "module x\n").unwrap();
        assert!(git(&dir, &["add", "go.mod"]));
        assert!(git(&dir, &["commit", "-q", "-m", "init"]));

        // Intact right after checkout.
        assert!(worktree_intact(&dir));

        // Simulate the interrupted-checkout damage seen in the wild.
        std::fs::remove_file(dir.join("go.mod")).unwrap();
        assert!(!worktree_intact(&dir), "deleted tracked file must fail");

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn missing_repo_is_not_intact() {
        let dir = std::env::temp_dir().join(format!("dgp-intact-none-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        assert!(!worktree_intact(&dir));
    }

    /// Go's module cache extracts with mode-0555 directories; plain
    /// remove_dir_all fails on them, force_remove_all must not.
    #[test]
    fn force_remove_handles_readonly_dirs() {
        use std::os::unix::fs::PermissionsExt;
        let dir = std::env::temp_dir().join(format!("dgp-ro-rm-{}", std::process::id()));
        let ro = dir.join(".ghost-cache/go-mod/pkg/mod/toolchain");
        std::fs::create_dir_all(&ro).unwrap();
        std::fs::write(ro.join("go"), "binary").unwrap();
        std::fs::set_permissions(&ro, std::fs::Permissions::from_mode(0o555)).unwrap();
        std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o755)).unwrap();

        // Precondition: the std helper really chokes on this layout.
        assert!(
            std::fs::remove_dir_all(&dir).is_err(),
            "0555 dir must block std removal"
        );

        assert!(force_remove_all(&dir).is_ok());
        assert!(!dir.exists());
    }

    fn which_git() -> bool {
        std::env::var_os("PATH")
            .map(|p| std::env::split_paths(&p).any(|dir| dir.join("git").is_file()))
            .unwrap_or(false)
    }
}
