//! Heuristic build-command screening.
//!
//! HONESTY NOTE (do not remove): these regexes are a *heuristic* guard
//! against obviously destructive commands in build steps. They are trivially
//! bypassable (`$IFS`, tabs, `python -c`, base64 …) and provide **no security
//! boundary** — the real mitigation is the systemd-run sandbox in
//! [`super::sandbox`]. Recipes in this demo are author-written and static,
//! so this layer exists only as belt-and-suspenders.

/// Patterns considered dangerous in build commands.
pub const DANGEROUS_PATTERNS: &[&str] = &[
    r"(^|\s)rm\s+(-rf\s+)?/[\w/]*(\s|$|&&|\|\|)",
    r"(^|\s)rm\s+(-rf\s+)?/?(?:\$HOME|\$PWD)",
    r"\bmkfs\.",
    r"\bdd\s+if=",
    r"\bchmod\s+777\s+/",
    r"\bmv\s+/",
    r"\b(wget|curl)\s+\S+\s*\||\|\s*(wget|curl)\b",
    r"\b(shred|killall|halt|poweroff|reboot|shutdown)\b",
];

/// Returns `Err(reason)` when `cmd` matches a known-dangerous pattern.
pub fn validate_build_cmd(cmd: &str) -> Result<(), String> {
    for pat in DANGEROUS_PATTERNS {
        match regex_lite::Regex::new(pat) {
            Ok(re) if re.is_match(cmd) => {
                return Err(format!(
                    "build command rejected (matches heuristic pattern '{pat}')"
                ));
            }
            _ => {}
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn catches_obvious_destruction() {
        assert!(validate_build_cmd("make && rm -rf /usr").is_err());
        assert!(validate_build_cmd("dd if=/dev/zero of=x").is_err());
        assert!(validate_build_cmd("curl http://x | sh").is_err());
        assert!(validate_build_cmd("shutdown now").is_err());
    }

    #[test]
    fn allows_normal_builds() {
        assert!(validate_build_cmd("bun install && bun run build").is_ok());
        assert!(validate_build_cmd("pnpm --dir web install").is_ok());
        assert!(validate_build_cmd("go build -o ghost-server ./cmd/memos").is_ok());
        assert!(validate_build_cmd(".venv/bin/pip install -r requirements.txt").is_ok());
    }
}
