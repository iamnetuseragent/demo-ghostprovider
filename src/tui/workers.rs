//! Worker threads for the TUI: scan, deployment, service management.

use std::sync::mpsc::Sender;

use super::Msg;
use crate::hoster::deploy;

pub(super) fn spawn_scan(tx: Sender<Msg>) {
    std::thread::spawn(move || {
        let result = crate::analyzer::probe::run_analysis();
        let mut out = String::new();
        let mark = |ok: bool| if ok { "[x]" } else { "[ ]" };
        out.push_str(&format!(
            "{} systemd          {}\n{} systemd-nspawn   {}\n{} git              {}\n{} python3 / node   {} / {}\n{} network          {}\n",
            mark(result.systemd),
            yesno(result.systemd, "found", "MISSING"),
            mark(result.systemd_nspawn),
            yesno(result.systemd_nspawn, "available", "not installed"),
            mark(result.git),
            yesno(result.git, "found", "MISSING"),
            mark(result.python3 && result.node),
            found("python3"),
            found("node"),
            mark(result.network),
            yesno(result.network, "online", "offline"),
        ));

        if !result.interfaces.is_empty() {
            out.push_str("\nInterfaces:\n");
            for i in &result.interfaces {
                out.push_str(&format!("  {:<14} {:<18} {}\n", i.name, i.ip, i.status));
            }
        }
        if !result.listening_ports.is_empty() {
            out.push_str("\nListening ports:\n");
            out.push_str("  PORT   PROCESS              FINGERPRINT\n");
            for p in &result.listening_ports {
                let desc = result
                    .services
                    .iter()
                    .find(|s| s.port == p.port)
                    .map_or_else(
                        || "—".to_string(),
                        |s| format!("{} ({})", s.service_name, s.confidence),
                    );
                out.push_str(&format!("  {:<6} {:<20} {}\n", p.port, p.process, desc));
            }
        }
        if !result.vpn_interfaces.is_empty() {
            out.push_str(&format!(
                "\nVPN active: {}\n",
                result.vpn_interfaces.join(", ")
            ));
        }
        for e in &result.errors {
            out.push_str(&format!("\n! {e}\n"));
        }
        let _ = tx.send(Msg::ScanDone(out));
    });
}

fn yesno(cond: bool, yes: &str, no: &str) -> String {
    if cond { yes.into() } else { no.into() }
}

fn found(bin: &str) -> String {
    if which(bin) {
        "found".into()
    } else {
        "missing".into()
    }
}

fn which(bin: &str) -> bool {
    std::env::var_os("PATH")
        .map(|p| std::env::split_paths(&p).any(|dir| dir.join(bin).is_file()))
        .unwrap_or(false)
}

pub(super) fn start_deployment(tx: Sender<Msg>, url: String) {
    std::thread::spawn(move || {
        let log_tx = tx.clone();
        let log = move |line: String| {
            let _ = log_tx.send(Msg::Log(line));
        };
        let ok = deploy::run_deployment(&url, &log) == deploy::DeployOutcome::Deployed;
        let _ = tx.send(Msg::DeployDone(ok));
    });
}

/// (unit name, status, url) rows for the services screen.
pub(super) fn service_rows() -> Vec<(String, String, String)> {
    crate::state::list()
        .into_iter()
        .map(|(name, entry)| {
            let active = std::process::Command::new("systemctl")
                .args(["--user", "is-active", &entry.unit_name])
                .output()
                .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
                .unwrap_or_else(|_| "unknown".into());
            let url = entry.urls.first().cloned().unwrap_or_default();
            (name, active, url)
        })
        .collect()
}

pub(super) fn service_action(name: &str, action: &str) -> String {
    let res = match action {
        "stop" => systemctl(&["--user", "stop", name]),
        "start" => systemctl(&["--user", "start", name]),
        "delete" => {
            deploy::remove_unit_and_state(name);
            return format!("{name}: deleted");
        }
        _ => Ok(()),
    };
    match res {
        Ok(()) => format!("{name}: {action}ed"),
        Err(e) => format!("{name}: {action} failed — {e}"),
    }
}

fn systemctl(args: &[&str]) -> anyhow::Result<()> {
    let out = std::process::Command::new("systemctl")
        .args(args)
        .output()?;
    if out.status.success() {
        Ok(())
    } else {
        anyhow::bail!("{}", String::from_utf8_lossy(&out.stderr).trim())
    }
}
