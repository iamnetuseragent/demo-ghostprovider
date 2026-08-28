//! demo-ghostprovider — local-first demo hosting panel (Rust rewrite).
//!
//! Deploys three curated services (VERT, SearXNG, Memos) as hardened
//! systemd user services. No telemetry: every outbound network contact is
//! allowlisted at compile time and logged locally (see [`netlog`]).

pub mod analyzer;
pub mod atomic;
pub mod flags;
pub mod hoster;
pub mod netlog;
pub mod output;
pub mod paths;
pub mod selftest;
pub mod serve;
pub mod state;
pub mod tui;
