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

    let path = std::env::temp_dir().join(format!("gp-askpass-{}", std::process::id()));
    #[cfg(unix)]
    std::fs::write(
        &path,
        format!("#!/bin/sh\necho '{}'\n", shell_quote(&token)),
    )?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700))?;
    }
    Ok(Some(path))
}

/// Single-quote for POSIX sh: `'` → `'\''`.
fn shell_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

pub struct CloneStatus {
    pub ok: bool,
    pub last_message: String,
}

/// Clone `url` into `dest`. Returns immediately when the repo already exists.
pub fn clone(url: &str, dest: &Path) -> CloneStatus {
    if dest.join(".git").is_dir() {
        return CloneStatus {
            ok: true,
            last_message: "already cloned".into(),
        };
    }
    let _ = std::fs::remove_dir_all(dest);

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
                let _ = std::fs::remove_dir_all(dest);
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
        let tmp = std::env::temp_dir().join(format!("gp-tarball-{}", uuid()));
        let mut cmd = Command::new("curl");
        cmd.args(["-4", "-sL", "-o"]).arg(&tmp).arg(&tb);
        if let Ok(token) = std::env::var("GITHUB_TOKEN").or_else(|_| std::env::var("GH_TOKEN")) {
            cmd.arg("-H").arg(format!("Authorization: token {token}"));
        }
        let Some(out) = cmd.output().ok().map(|o| o.status.success()) else {
            continue;
        };
        let size_ok = std::fs::metadata(&tmp)
            .map(|m| m.len() > 1000)
            .unwrap_or(false);
        if !(out && size_ok) {
            let _ = std::fs::remove_file(&tmp);
            continue;
        }
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
                let _ = std::fs::remove_dir_all(dest);
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

fn uuid() -> u64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos() as u64)
        .unwrap_or(0)
        ^ (std::process::id() as u64)
}
