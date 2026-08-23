//! System probing: prerequisite checks, network scan, port fingerprinting.

use std::io::{Read, Write};
use std::net::{Ipv4Addr, SocketAddr, SocketAddrV4, TcpStream};
use std::process::Command;
use std::time::Duration;

use super::models::{AnalysisResult, InterfaceInfo, ListeningPort, ServiceFingerprint};
use super::signatures;

fn which(bin: &str) -> bool {
    std::env::var_os("PATH")
        .map(|p| std::env::split_paths(&p).any(|dir| dir.join(bin).is_file()))
        .unwrap_or(false)
}

/// Try to fingerprint an HTTP service listening on 127.0.0.1:`port`.
pub fn fingerprint_port(port: u16) -> Option<ServiceFingerprint> {
    let mut sock = TcpStream::connect(SocketAddrV4::new(Ipv4Addr::LOCALHOST, port)).ok()?;
    sock.set_read_timeout(Some(Duration::from_secs(5))).ok()?;
    sock.set_write_timeout(Some(Duration::from_secs(3))).ok()?;
    sock.write_all(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")
        .ok()?;

    let mut buf = vec![0u8; 8192];
    let n = sock.read(&mut buf).unwrap_or(0);
    let response = String::from_utf8_lossy(&buf[..n]).into_owned();

    let headers_end = response.find("\r\n\r\n")?;
    let (headers_raw, body) = response.split_at(headers_end);
    let body = &body[4..];

    let status_line = headers_raw.lines().next().unwrap_or("").to_string();
    let server_header = headers_raw
        .lines()
        .find(|l| l.to_lowercase().starts_with("server:"))
        .and_then(|l| l.split_once(':'))
        .map(|(_, v)| v.trim().to_string())
        .unwrap_or_default();

    if let Some(sig) = signatures::match_body(body) {
        return Some(ServiceFingerprint {
            port,
            service_type: sig.service_type.into(),
            service_name: sig.service_name.into(),
            confidence: sig.confidence,
            server_header,
            status_line,
        });
    }

    // Fallback: classify by Server header.
    let lower = server_header.to_lowercase();
    let known = [
        "nginx", "apache", "caddy", "iis", "gunicorn", "uvicorn", "node", "express", "python",
    ];
    if known.iter().any(|k| lower.contains(k)) && !lower.is_empty() {
        let name = server_header.split('/').next().unwrap_or("Web server");
        return Some(ServiceFingerprint {
            port,
            service_type: "web_app".into(),
            service_name: title_case(name),
            confidence: 60,
            server_header,
            status_line,
        });
    }
    if !status_line.starts_with("HTTP/") {
        return None;
    }
    Some(ServiceFingerprint {
        port,
        service_type: "web_app".into(),
        service_name: "Unknown HTTP Service".into(),
        confidence: 30,
        server_header,
        status_line,
    })
}

fn title_case(s: &str) -> String {
    s.split(['-', ' '])
        .map(|w| {
            let mut cs = w.chars();
            match cs.next() {
                Some(f) => f.to_uppercase().collect::<String>() + cs.as_str(),
                None => String::new(),
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
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
        .filter_map(|line| {
            let parts: Vec<&str> = line.split_whitespace().collect();
            if parts.len() < 4 {
                return None;
            }
            let addr_port = parts[3];
            let (_, port_str) = addr_port.rsplit_once(':')?;
            let port = port_str.parse().ok()?;
            let process = line
                .rsplit("users:((\"")
                .next()
                .and_then(|r| r.split('"').next())
                .unwrap_or("")
                .to_string();
            Some(ListeningPort {
                port,
                address: addr_port.to_string(),
                process,
            })
        })
        .collect()
}

fn is_vpn_iface(name: &str) -> bool {
    ["tun", "tap", "wg", "ppp", "vpn", "virbr"]
        .iter()
        .any(|kw| name.to_lowercase().contains(kw))
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
    let services = ports
        .iter()
        .filter_map(|p| fingerprint_port(p.port))
        .collect();

    let vpn_interfaces: Vec<String> = interfaces
        .iter()
        .filter(|i| is_vpn_iface(&i.name))
        .map(|i| i.name.clone())
        .collect();

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
        services,
        vpn_active: !vpn_interfaces.is_empty(),
        vpn_interfaces,
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
