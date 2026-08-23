//! demo-ghostprovider — local-first demo hosting panel.
//!
//! CLI surface:
//!   (no args)            launch the TUI
//!   --show-endpoints     print the compiled-in endpoint allowlist plus this
//!                        session's request counters, then exit
//!   --version | -V       print version
//!   --selftest           E2E check against the live systemd user manager:
//!                        installs a real unit running the static server,
//!                        polls activation, health-checks it, cleans up
//!   __serve-static DIR PORT   internal: static server used by deployed units

use anyhow::Context;

use demo_ghostprovider::{netlog, selftest, serve};

fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();

    match args.first().map(String::as_str) {
        Some("--version" | "-V") => {
            println!("demo-ghostprovider {}", env!("CARGO_PKG_VERSION"));
        }
        Some("--show-endpoints") => {
            println!("Compiled-in remote allowlist:");
            for h in netlog::ALLOWED_ENDPOINTS {
                println!("  {h}");
            }
            println!("Local health-check hosts (never used for API calls):");
            for h in netlog::LOCAL_ENDPOINTS {
                println!("  {h}");
            }
            let summary = netlog::session_summary();
            if summary.is_empty() {
                println!("\nNo outbound requests made this session.");
            } else {
                println!("\nThis session:");
                for (host, (total, errors)) in &summary {
                    println!("  {host}: {total} request(s), {errors} with errors");
                }
            }
        }
        Some("--selftest") => {
            selftest::run()?;
        }
        // Internal subcommand used by generated systemd units. Not advertised.
        Some("__serve-static") => {
            let dir = args.get(1).context("usage: __serve-static DIR PORT")?;
            let port: u16 = args
                .get(2)
                .context("usage: __serve-static DIR PORT")?
                .parse()?;
            serve::serve_static(std::path::Path::new(dir), port)?;
        }
        // Internal subcommand for scripted E2E: full pipeline without the TUI.
        Some("__deploy") => {
            let url = args.get(1).context("usage: __deploy GITHUB_URL")?;
            let outcome = demo_ghostprovider::hoster::deploy::run_deployment(url, &|line| {
                println!("{line}");
            });
            match outcome {
                demo_ghostprovider::hoster::deploy::DeployOutcome::Deployed => {}
                other => {
                    eprintln!("deploy failed: {other:?}");
                    std::process::exit(1);
                }
            }
        }
        Some(other) if other.starts_with("--") => {
            eprintln!("unknown option: {other}\nsee --help");
            std::process::exit(2);
        }
        Some("--help" | "-h") => print_help(),
        _ => {
            demo_ghostprovider::tui::run()?;
        }
    }
    Ok(())
}

fn print_help() {
    println!(
        "demo-ghostprovider {} — deploy three curated services as hardened systemd user units\n\
         \n\
         Usage:\n\
         \x20 demo-ghostprovider              launch the interactive panel\n\
         \x20 demo-ghostprovider --show-endpoints   transparency: allowlist + session counters\n\
         \x20 demo-ghostprovider --selftest           verify systemd integration on this machine\n\
         \x20 demo-ghostprovider --version          version",
        env!("CARGO_PKG_VERSION")
    );
}
