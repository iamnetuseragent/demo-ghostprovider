//! Free TCP port selection by *actually binding*.
//!
//! The Python original probed ports with `connect_ex`, which (a) races
//! (TOCTOU) and (b) misses listeners on other interfaces. Here we bind the
//! socket for real; the inherent close→service-bind race window is the same
//! as any ephemeral-port allocator and is documented in README.

use std::net::{Ipv4Addr, SocketAddrV4, TcpListener};

/// Find an available loopback port.
///
/// * `start == 0` — pick a random port in `[8000, 30000)` to avoid colliding
///   with commonly used fixed ports.
/// * `start > 0` — scan `[start, start + max_tries)`.
pub fn find_free_port(start: u16, max_tries: u16) -> anyhow::Result<u16> {
    let mut start = start;
    if start == 0 {
        // No rand crate: derive from time+pid hash.
        let seed = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.subsec_nanos() ^ d.as_secs() as u32)
            .unwrap_or(0)
            ^ (std::process::id());
        start = 8000 + (seed % 22_000) as u16;
    }

    for port in start..start.saturating_add(max_tries) {
        if bind_ok(port) {
            return Ok(port);
        }
    }
    anyhow::bail!("no free port found in range {start}-{}", start + max_tries)
}

fn bind_ok(port: u16) -> bool {
    let addr = SocketAddrV4::new(Ipv4Addr::LOCALHOST, port);
    TcpListener::bind(addr).is_ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn finds_bindable_port() {
        let p = find_free_port(0, 50).unwrap();
        assert!((8000..30000).contains(&p));
        // Port was released before returning; must be re-bindable.
        assert!(bind_ok(p));
    }

    #[test]
    fn skips_occupied_port() {
        let listener = TcpListener::bind(SocketAddrV4::new(Ipv4Addr::LOCALHOST, 0)).unwrap();
        let taken = listener.local_addr().unwrap().port();
        let p = find_free_port(taken, 5).unwrap();
        assert_ne!(p, taken);
    }
}
