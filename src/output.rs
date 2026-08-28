//! Styled terminal output for the scripted deploy path (`__deploy`).
//!
//! Mirrors the TUI's log classification so both surfaces read the same:
//! dim progress steps, a TOOL DOCTOR banner with structured fix commands,
//! green listening lines, red failures. Colors auto-disable when stdout is
//! not a terminal or `NO_COLOR` is set — piped output stays machine-clean.

use std::io::IsTerminal;

use crate::hoster::toolcheck;

const RESET: &str = "\x1b[0m";
const BOLD: &str = "\x1b[1m";
const DIM: &str = "\x1b[2m";
// Same palette as src/tui/mod.rs.
const ACCENT: &str = "\x1b[38;2;64;220;255m";
const OK: &str = "\x1b[38;2;80;250;123m";
const WARN: &str = "\x1b[38;2;255;184;0m";
const ERR: &str = "\x1b[38;2;255;85;85m";

pub struct Painter {
    enabled: bool,
    in_doctor: bool,
}

impl Default for Painter {
    fn default() -> Self {
        Self::new()
    }
}

impl Painter {
    pub fn new() -> Self {
        Self {
            enabled: std::env::var_os("NO_COLOR").is_none() && std::io::stdout().is_terminal(),
            in_doctor: false,
        }
    }

    fn paint(&self, code: &str, text: &str) -> String {
        if self.enabled {
            format!("{code}{text}{RESET}")
        } else {
            text.to_string()
        }
    }

    /// Header emitted once before the pipeline starts.
    pub fn header(&self, url: &str) -> String {
        format!(
            " {} {}",
            self.paint(DIM, "deploying"),
            self.paint(BOLD, url)
        )
    }

    /// Classify one pipeline log line into zero or more styled output lines.
    pub fn render(&mut self, line: &str) -> Vec<String> {
        let t = line.trim_start();
        if let Some(body) = t.strip_prefix("! ") {
            if toolcheck::is_issue_line(t) {
                let mut out = Vec::new();
                if !self.in_doctor {
                    out.push(String::new());
                    out.push(format!(
                        " {} {}",
                        self.paint(WARN, "──"),
                        self.paint(BOLD, "TOOL DOCTOR")
                    ));
                    self.in_doctor = true;
                }
                if let Some((problem, cmd, note)) = toolcheck::split_issue(body) {
                    out.push(format!(
                        " {} {}",
                        self.paint(BOLD, "!"),
                        self.paint(ERR, problem)
                    ));
                    out.push(format!(
                        "   {} {}",
                        self.paint(DIM, "fix »"),
                        self.paint(ACCENT, cmd)
                    ));
                    if let Some(n) = note {
                        out.push(format!("      {}", self.paint(DIM, n)));
                    }
                    return out;
                }
            }
            self.in_doctor = false;
            return vec![format!(
                " {} {}",
                self.paint(ERR, "x"),
                self.paint(ERR, body)
            )];
        }
        self.in_doctor = false;
        if let Some(body) = t.strip_prefix("warn: ") {
            return vec![format!(
                " {} {}",
                self.paint(WARN, "!"),
                self.paint(WARN, body)
            )];
        }
        if let Some(url) = t.strip_prefix("listening on ") {
            return vec![format!(
                " {} {}",
                self.paint(OK, "+"),
                format!("listening on {}", self.paint(BOLD, url))
            )];
        }
        if t.contains("failed") || t.contains("ERROR") {
            return vec![format!(" {}", self.paint(ERR, line))];
        }
        if t.ends_with("...") {
            return vec![format!(" {} {}", self.paint(DIM, "·"), self.paint(DIM, t))];
        }
        vec![format!(" {line}")]
    }

    /// Final verdict line after the pipeline returns.
    pub fn summary(&self, ok: bool) -> String {
        if ok {
            format!(
                " {} {}",
                self.paint(OK, "+"),
                self.paint(BOLD, "deployment complete")
            )
        } else {
            format!(
                " {} {}",
                self.paint(ERR, "x"),
                self.paint(BOLD, "deployment failed")
            )
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn disabled_painter_emits_plain_text() {
        let mut p = Painter {
            enabled: false,
            in_doctor: false,
        };
        assert_eq!(
            p.render("cloning repository..."),
            vec![" · cloning repository..."]
        );
        assert_eq!(
            p.render("! invalid GitHub URL format"),
            vec![" x invalid GitHub URL format"]
        );
    }

    #[test]
    fn doctor_banner_is_emitted_once_per_group() {
        let mut p = Painter {
            enabled: false,
            in_doctor: false,
        };
        let issue =
            "! Memos needs Go >= 1.27.0, found 1.26.6 — update first: sudo pacman -S --needed go";
        let first = p.render(issue);
        assert!(first.iter().any(|l| l.contains("TOOL DOCTOR")));
        assert!(
            first
                .iter()
                .any(|l| l.contains("fix » sudo pacman -S --needed go"))
        );
        // Second consecutive issue: no second banner.
        let second = p.render("! X needs Bun >= 1.1.0 but 'bun' is not installed — install: curl -fsSL https://bun.sh/install | bash");
        assert!(!second.iter().any(|l| l.contains("TOOL DOCTOR")));
        // Non-issue line closes the group.
        let _ = p.render("build: bun install");
        let third = p.render(issue);
        assert!(third.iter().any(|l| l.contains("TOOL DOCTOR")));
    }

    #[test]
    fn listening_line_is_green_with_bold_url() {
        let mut p = Painter {
            enabled: false,
            in_doctor: false,
        };
        assert_eq!(
            p.render("listening on http://localhost:8888"),
            vec![" + listening on http://localhost:8888".to_string()]
        );
    }
}
