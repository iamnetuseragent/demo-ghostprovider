//! End-to-end self-test against the live systemd user manager.
//!
//! Installs a real transient-style user unit that runs THIS binary's static
//! server, waits for activation through the same polling used by deploys,
//! performs a loopback health check, and cleans up. Exercises the full
//! unit-generation → start → verify path without network access.

use std::path::Path;

use anyhow::{Context, bail};

use crate::hoster::port::find_free_port;
use crate::hoster::units::{self, StartOutcome, UnitSpec};

pub fn run() -> anyhow::Result<()> {
    let root = std::env::temp_dir().join("gp-selftest-site");
    std::fs::create_dir_all(&root)?;
    std::fs::write(
        root.join("index.html"),
        "<html><body>ghostprovider self-test OK</body></html>",
    )?;

    let port = find_free_port(28100, 200)?;
    let self_exe = std::env::current_exe()?;
    let exec_start = format!(
        "{} __serve-static {} {port}",
        self_exe.display(),
        root.display()
    );

    let spec = UnitSpec {
        service_name: "gp-selftest",
        working_dir: Path::new("/tmp"),
        exec_start: &exec_start,
        description: "demo-ghostprovider self-test",
        env_file: None,
        extra_env: &[],
        loopback_only: true,
    };
    units::create_unit(&spec).context("unit creation")?;

    let out = std::process::Command::new("systemctl")
        .args(["--user", "start", "--no-block", "gp-selftest"])
        .status()
        .context("systemctl start")?;
    if !out.success() {
        bail!("systemctl start failed");
    }

    println!("waiting for activation (polling)...");
    let mut attempts = 0;
    let outcome = units::wait_until_active("gp-selftest");
    if outcome != StartOutcome::Active {
        let logs = units::service_logs("gp-selftest", 20);
        units::remove_unit("gp-selftest");
        bail!("service did not activate ({outcome:?}):\n{logs}");
    }

    // Loopback health check (recorded in the local net log).
    // systemd flips to "active" as soon as exec(2) succeeds; the listener
    // may need a moment to bind, so poll briefly like real deploys do.
    let url = format!("http://127.0.0.1:{port}/");
    let mut last_err;
    let body = loop {
        match reqwest_get(&url) {
            Ok(b) => break Ok(b),
            Err(e) => {
                last_err = e;
                std::thread::sleep(std::time::Duration::from_millis(300));
            }
        }
        attempts += 1;
        if attempts >= 15 {
            break Err(last_err);
        }
    };
    match body {
        Ok(b) if b.contains("self-test OK") => {
            crate::netlog::record("127.0.0.1", "/", Ok(200));
            println!("health check {url}: OK");
        }
        Ok(b) => {
            crate::netlog::record("127.0.0.1", "/", Ok(200));
            units::remove_unit("gp-selftest");
            bail!("unexpected body: {b}");
        }
        Err(e) => {
            crate::netlog::record("127.0.0.1", "/", Err(e.clone()));
            units::remove_unit("gp-selftest");
            bail!("health check failed: {e}");
        }
    };

    units::remove_unit("gp-selftest");
    let _ = std::fs::remove_dir_all(&root);
    println!("SELFTEST PASS");
    Ok(())
}

/// Minimal loopback HTTP GET using only std.
fn reqwest_get(url: &str) -> Result<String, String> {
    use std::io::{Read, Write};
    use std::net::TcpStream;
    use std::time::Duration;

    let addr = url
        .strip_prefix("http://")
        .and_then(|rest| rest.split('/').next().map(|s| s.to_string()))
        .ok_or_else(|| "bad url".to_string())?;
    let mut stream = TcpStream::connect(addr.clone()).map_err(|e| e.to_string())?;
    stream.set_read_timeout(Some(Duration::from_secs(5))).ok();
    stream
        .write_all(format!("GET / HTTP/1.0\r\nHost: {addr}\r\n\r\n").as_bytes())
        .map_err(|e| e.to_string())?;
    let mut buf = String::new();
    stream.read_to_string(&mut buf).map_err(|e| e.to_string())?;
    Ok(buf
        .split_once("\r\n\r\n")
        .map(|(_, b)| b.to_string())
        .unwrap_or(buf))
}
