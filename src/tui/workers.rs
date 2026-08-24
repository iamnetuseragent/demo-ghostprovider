//! Worker threads for the TUI: scan, deployment, service management.

use std::collections::HashMap;
use std::sync::mpsc::Sender;

use super::Msg;
use crate::hoster::deploy;

/// Port → unit name for every URL registered by our deployments. Purely a
/// local state.json lookup — no probing involved.
fn deployed_port_map(entries: &[(String, crate::state::ServiceEntry)]) -> HashMap<u16, String> {
    let mut map = HashMap::new();
    for (_, entry) in entries {
        for url in &entry.urls {
            if let Some((_, port)) = url.rsplit_once(':') {
                if let Ok(port) = port.parse::<u16>() {
                    map.insert(port, entry.unit_name.clone());
                }
            }
        }
    }
    map
}

/// Table label for a listening port. Registered deployments win over the raw
/// process comm name: VERT's static server would otherwise show up as
/// "demo-ghostprovi", hiding which deployed service owns the port.
fn process_label(process: &str, port: u16, deployed: &HashMap<u16, String>) -> String {
    match deployed.get(&port) {
        Some(unit) => format!("{unit} (deployed)"),
        None => process.to_string(),
    }
}

pub(super) fn spawn_scan(tx: Sender<Msg>) {
    std::thread::spawn(move || {
        let result = crate::analyzer::probe::run_analysis();
        let deployed = deployed_port_map(&crate::state::list());
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
            // Header built from the same format string as the rows below:
            // the two can never drift apart.
            out.push_str(&format!("  {:<6} {}\n", "PORT", "PROCESS"));
            for p in &result.listening_ports {
                out.push_str(&format!(
                    "  {:<6} {}\n",
                    p.port,
                    process_label(&p.process, p.port, &deployed)
                ));
            }
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::state::ServiceEntry;

    fn entry(unit: &str, urls: &[&str]) -> (String, ServiceEntry) {
        (
            unit.to_string(),
            ServiceEntry {
                unit_name: unit.to_string(),
                project_dir: format!("/tmp/{unit}"),
                url: "https://example.com/x".into(),
                urls: urls.iter().map(|s| s.to_string()).collect(),
            },
        )
    }

    #[test]
    fn port_map_parses_urls_and_skips_garbage() {
        let entries = vec![
            entry("demo-vert", &["http://localhost:10748"]),
            entry(
                "demo-memos",
                &["http://localhost:8075", "not-a-url", "ftp://x"],
            ),
        ];
        let map = deployed_port_map(&entries);
        assert_eq!(map.get(&10748).unwrap(), "demo-vert");
        assert_eq!(map.get(&8075).unwrap(), "demo-memos");
        assert!(map.len() == 2);
    }

    #[test]
    fn deployed_ports_override_process_name() {
        let entries = vec![entry("demo-vert", &["http://localhost:10748"])];
        let map = deployed_port_map(&entries);

        assert_eq!(
            process_label("demo-ghostprovi", 10748, &map),
            "demo-vert (deployed)"
        );
        // Unrelated port keeps the ss-provided process name.
        assert_eq!(process_label("tor", 9050, &map), "tor");
        assert_eq!(process_label("(system)", 5432, &map), "(system)");
    }
}
