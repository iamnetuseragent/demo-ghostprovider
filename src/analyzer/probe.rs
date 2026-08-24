//! System probing: prerequisite checks and a bare map of occupied ports.
//!
//! Privacy policy: this report must stay useless to attackers. No VPN
//! interface detection, no HTTP probing of local services — the port table
//! names only what `ss` itself already shows to the local user.

use std::net::{Ipv4Addr, SocketAddr, SocketAddrV4, TcpStream};
use std::process::Command;
use std::time::Duration;

use super::models::{AnalysisResult, InterfaceInfo, ListeningPort};

fn which(bin: &str) -> bool {
    std::env::var_os("PATH")
        .map(|p| std::env::split_paths(&p).any(|dir| dir.join(bin).is_file()))
        .unwrap_or(false)
}

fn detect_interfaces() -> Vec<InterfaceInfo> {
    let Ok(out) = Command::new("ip").args(["-br", "addr", "show"]).output() else {
        return vec![];
    };
    if !out.status.success() {
        return vec![];
    }
    String::from_utf8_lossy(&out.stdout)
        .lines()
        .filter_map(|line| {
            let parts: Vec<&str> = line.split_whitespace().collect();
            if parts.len() < 2 {
                return None;
            }
            let name = parts[0].to_string();
            let status = if parts[1] == "UP" || parts[1] == "UNKNOWN" {
                "up"
            } else {
                "down"
            }
            .to_string();
            let ip_info = parts.get(2).copied().unwrap_or("");
            let ip = ip_info.split('/').next().unwrap_or("").to_string();
            let netmask = format!("/{}", ip_info.split('/').nth(1).unwrap_or(""));
            Some(InterfaceInfo {
                name,
                ip,
                netmask,
                status,
            })
        })
        .collect()
}

/// Audit lesson applied: unlike the Python version this respects the exit
/// code instead of returning true whenever `ping` exists.
fn ping_ok(host: &str) -> bool {
    Command::new("ping")
        .args(["-c", "1", "-W", "2", host])
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

/// Parse one `ss -tlnp4` data row.
///
/// For sockets owned by other users (system daemons under root) `ss -p`
/// prints NO `users:((...))` section at all; the raw line must never leak
/// into the PROCESS column — such rows get the `(system)` label instead.
pub fn parse_ss_row(line: &str) -> Option<ListeningPort> {
    let parts: Vec<&str> = line.split_whitespace().collect();
    if parts.len() < 4 {
        return None;
    }
    let addr_port = parts[3];
    let (_, port_str) = addr_port.rsplit_once(':')?;
    let port = port_str.parse().ok()?;
    let process = if line.contains("users:((\"") {
        // Rightmost owner wins (sockets can be shared between processes).
        line.rsplit("users:((\"")
            .next()
            .and_then(|r| r.split('"').next())
            .unwrap_or("(system)")
            .to_string()
    } else {
        "(system)".to_string()
    };
    Some(ListeningPort {
        port,
        address: addr_port.to_string(),
        process,
    })
}

fn detect_listening_ports() -> Vec<ListeningPort> {
    let Ok(out) = Command::new("ss").args(["-tlnp4"]).output() else {
        return vec![];
    };
    if !out.status.success() {
        return vec![];
    }
    String::from_utf8_lossy(&out.stdout)
        .lines()
        .skip(1)
        .filter_map(parse_ss_row)
        .collect()
}

fn get_gateway() -> String {
    let Ok(out) = Command::new("ip")
        .args(["route", "show", "default"])
        .output()
    else {
        return String::new();
    };
    String::from_utf8_lossy(&out.stdout)
        .split_whitespace()
        .nth(2)
        .unwrap_or("")
        .to_string()
}

fn get_dns() -> Vec<String> {
    let Ok(content) = std::fs::read_to_string("/etc/resolv.conf") else {
        return vec![];
    };
    content
        .lines()
        .filter_map(|l| l.strip_prefix("nameserver ").map(|s| s.trim().to_string()))
        .collect()
}

/// Run the full local analysis.
pub fn run_analysis() -> AnalysisResult {
    let interfaces = detect_interfaces();
    let ports = detect_listening_ports();

    let localhost_ok = [80u16, 8080].iter().any(|p| {
        TcpStream::connect_timeout(
            &SocketAddr::from(SocketAddrV4::new(Ipv4Addr::LOCALHOST, *p)),
            Duration::from_secs(2),
        )
        .is_ok()
    });

    let mut result = AnalysisResult {
        systemd: which("systemctl"),
        systemd_nspawn: which("systemd-nspawn"),
        git: which("git"),
        python3: which("python3"),
        node: which("node"),
        localhost: localhost_ok,
        network: ping_ok("127.0.0.1")
            || ping_ok("192.168.0.1")
            || std::net::ToSocketAddrs::to_socket_addrs(&("github.com", 443)).is_ok(),
        interfaces,
        listening_ports: ports,
        gateway: get_gateway(),
        dns: get_dns(),
        errors: vec![],
    };

    if !result.systemd {
        result
            .errors
            .push("systemd not found — required for service management".into());
    }
    if !result.systemd_nspawn {
        result
            .errors
            .push("systemd-nspawn not found — recommended for isolated hosting".into());
    }
    if !result.git {
        result
            .errors
            .push("Git not found — cannot clone repositories".into());
    }
    if !result.network {
        result
            .errors
            .push("No network — cannot fetch remote repositories".into());
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ss_row_with_owner_extracts_process() {
        let line = "LISTEN 0      128        0.0.0.0:5222       0.0.0.0:*    users:((\"socat\",pid=1234,fd=5))";
        let p = parse_ss_row(line).expect("parses");
        assert_eq!(p.port, 5222);
        assert_eq!(p.process, "socat");
        assert_eq!(p.address, "0.0.0.0:5222");
    }

    /// Sockets owned by other users have no users:(()) section; the raw line
    /// must never leak into the PROCESS column.
    #[test]
    fn ss_row_without_owner_is_labeled_system() {
        let line = "LISTEN 0      200        127.0.0.1:5432       0.0.0.0:*";
        let p = parse_ss_row(line).expect("parses");
        assert_eq!(p.port, 5432);
        assert_eq!(p.process, "(system)");
    }

    #[test]
    fn ss_row_garbage_is_rejected() {
        assert!(parse_ss_row("").is_none());
        assert!(parse_ss_row("State Recv-Q Send-Q").is_none());
        assert!(parse_ss_row("LISTEN 0 0 notaport 0.0.0.0:*").is_none());
    }
}
