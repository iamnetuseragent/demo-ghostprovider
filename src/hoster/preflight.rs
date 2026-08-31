//! Pre-flight environment checks before deployment.
//!
//! Audit lesson applied: besides systemd/nspawn/network this now verifies
//! that the *selected recipe's build tools* are installed — the old check
//! stayed green and then failed deep inside `bun install` / `go build`.

use std::process::Command;

/// One human-readable issue per failed check; empty slice = green light.
pub fn preflight_check(tools: &[&str]) -> Vec<String> {
    let mut issues = Vec::new();

    // ── systemd (user manager) ──
    match Command::new("systemctl")
        .args(["--user", "is-system-running"])
        .output()
    {
        Err(_) => issues.push("systemd not found".into()),
        Ok(out) => {
            let stdout = String::from_utf8_lossy(&out.stdout);
            if !out.status.success() && !stdout.contains("degraded") {
                issues.push("systemd not running properly".into());
            }
        }
    }

    // ── systemd-nspawn ──
    match Command::new("systemd-nspawn").arg("--version").output() {
        Err(_) => issues.push("systemd-nspawn not installed".into()),
        Ok(out) if !out.status.success() => issues.push("systemd-nspawn not available".into()),
        Ok(_) => {}
    }

    // ── network: github.com is what we actually need for clone/API.
    // HEAD probe (no body to read), recorded in the local net log. ──
    if !super::httpclient::head_ok("https://github.com/") {
        issues.push("No network connectivity (https://github.com unreachable)".into());
    }

    // ── per-recipe build tools ──
    for tool in tools {
        if !which(tool) {
            issues.push(format!(
                "'{tool}' not found on PATH — required by this service's build (install hint: {})",
                install_hint(tool)
            ));
        }
    }

    issues
}

pub fn which(bin: &str) -> bool {
    std::env::var_os("PATH")
        .map(|p| std::env::split_paths(&p).any(|dir| dir.join(bin).is_file()))
        .unwrap_or(false)
}

fn install_hint(tool: &str) -> &'static str {
    match tool {
        "bun" => "see https://bun.sh (curl -fsSL https://bun.sh/install | bash)",
        "pnpm" => "corepack enable pnpm  OR  npm install -g pnpm",
        "go" => "install Go from your package manager or https://go.dev/dl/",
        "python3" => "install Python >= 3.10 from your package manager",
        "node" => "install Node.js from your package manager or https://nodejs.org",
        _ => "install it with your package manager",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_tool_is_reported_with_hint() {
        // Network/systemd checks depend on the host; assert only the tool one.
        let issues = preflight_check(&["definitely-not-a-real-binary-xyz"]);
        let tool_issue = issues
            .iter()
            .find(|i| i.contains("definitely-not-a-real-binary-xyz"));
        assert!(
            tool_issue.is_some_and(|i| i.contains("install hint")),
            "{issues:?}"
        );
    }

    #[test]
    fn present_tool_passes_that_check() {
        // `sh` exists everywhere this program runs; only tool checks are
        // asserted here because systemd/network depend on the host env.
        assert!(which("sh"));
    }
}
