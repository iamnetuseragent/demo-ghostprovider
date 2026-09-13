//! demo-test — test version with redesigned TUI (red-on-black theme).

#![deny(unsafe_code)]

fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("--version" | "-V") => {
            println!("demo-test {}", env!("CARGO_PKG_VERSION"));
        }
        Some("--help" | "-h") => {
            println!(
                "demo-test {} — test build with redesigned TUI\n\
                 \n\
                 Usage:\n\
                 \x20 demo-test              launch the redesigned panel",
                env!("CARGO_PKG_VERSION")
            );
        }
        _ => {
            demo_ghostprovider::tui_v2::run()?;
        }
    }
    Ok(())
}
