//! Curated demo catalog — a small set of supported services.
//!
//! demo_ghostprovider does not host arbitrary repositories. Each entry is a
//! hardcoded deploy recipe for one specific public service. `tools` lists the
//! executables the recipe's build steps require; preflight refuses to start
//! a deployment when any of them is missing (lesson from the audit: the old
//! preflight was green while `bun install` would fail with ENOENT).

/// A hardcoded deploy recipe for a single supported demo service.
#[derive(Debug, Clone)]
pub struct DemoRecipe {
    pub owner: &'static str,
    pub name: &'static str,
    pub language: &'static str,
    pub service_name: &'static str,
    pub description: &'static str,
    pub display_name: &'static str,
    /// Pinned commit SHA that a deployment checks out after cloning, so a
    /// recipe never silently tracks whatever `main`/`master` moved to at
    /// build time (anti-TOFU). Bumped consciously when a recipe is updated.
    pub commit: &'static str,
    pub pre_build: &'static [&'static str],
    pub build_steps: &'static [&'static str],
    /// Host-phase dependency pre-fetch steps, run BEFORE the sandboxed build
    /// with network available (see `prefetch.rs`). These fill the tool caches
    /// so each sandboxed build step below runs fully offline. They are
    /// *downloader* commands, never the project's build code — a hostile
    /// `setup.py`/`postinstall` is not executed on the host (see the security
    /// invariant at the top of `prefetch.rs`).
    pub prefetch_steps: &'static [&'static str],
    /// Host-phase pinned paraglide-js plugin seeds (VERT recipe only; empty
    /// for the others). Each entry is `(jsdelivr module URL as written in
    /// `project.inlang/settings.json`, lowercase-hex SHA-256 of the file
    /// jsdelivr must serve at that URL)`. Fetched through the allowlisted
    /// client (`cdn.jsdelivr.net` — a permitted, net.log-visible host) and
    /// content-pinned so a silent version lift under the same `@N` major tag
    /// fails the deploy instead of feeding changed third-party code into the
    /// offline build.
    pub plugins: &'static [(&'static str, &'static str)],
    /// Placeholders: {bin} {venv} {python} {project} {port} {self}
    /// {self} expands to this binary — used by the built-in static server.
    pub start_cmd: &'static str,
    /// 0 = pick a random free port.
    pub port: u16,
    pub searxng: bool,
    /// Executables required on PATH for the build steps to succeed.
    pub tools: &'static [&'static str],
    /// Runtime needs no outbound network (a built-in static server for
    /// example): the unit gets `IPAddressAllow=loopback` so a compromised
    /// build output can never call out to the internet.
    pub loopback_only: bool,
}

pub const DEMO_SERVICES: &[DemoRecipe] = &[
    DemoRecipe {
        owner: "VERT-sh",
        name: "VERT",
        language: "JavaScript",
        service_name: "demo-vert",
        description: "VERT — next-generation file converter (Svelte)",
        display_name: "VERT",
        commit: "cc7b5a54d5e9c797b377db47b9bdfbb561707783",
        pre_build: &["if [ -f .env.example ] && [ ! -f .env ]; then cp .env.example .env; fi"],
        // bun install fills node_modules/caches; the two paraglide-js plugin
        // modules are seeded separately (see `plugins` below and
        // `prefetch.rs::seed_paraglide_plugins`). Both the shell prefetch and
        // the Rust plugin seed run before the sandboxed build, which itself
        // must be fully offline (PrivateNetwork=yes — see sandbox.rs).
        prefetch_steps: &[
            "bun install --frozen-lockfile",
        ],
        // Pinned (url, sha256) paraglide-js plugin modules.  Fetched through
        // the allowlisted client (net.log-visible) and verified against these
        // digests *before* any file is placed — a CDN content drift under the
        // same `@N` major tag fails the deploy instead of feeding silently
        // changed code into the offline build. The URLs are identical to
        // project.inlang/settings.json so the FNV1a-64 filenames match (see
        // prefetch.rs::paraglide_cache_name and the guard tests there).
        plugins: &[
            (
                "https://cdn.jsdelivr.net/npm/@inlang/plugin-message-format@4/dist/index.js",
                "b22cf60eb28b3c8c3ce1fb6300611a0552f12d0d995d37c4dd2c96e3ad80c645",
            ),
            (
                "https://cdn.jsdelivr.net/npm/@inlang/plugin-m-function-matcher@2/dist/index.js",
                "85862f6305793b56bfd9afe5368b096e63fb2aeab38b7799c051517be3499c0b",
            ),
        ],
        // PrivateNetwork is enforced: deps come ONLY from the host prefetch
        // (bun install + the pinned plugin seed above). The sandboxed build
        // itself is fully offline.
        build_steps: &["bun run build"],
        // Served by THIS binary (built-in static server) instead of shelling
        // out to `python -m http.server`: one less host dependency.
        start_cmd: "{self} __serve-static {project}/build {port}",
        port: 0,
        searxng: false,
        tools: &["bun"],
        loopback_only: true,
    },
    DemoRecipe {
        owner: "searxng",
        name: "searxng",
        language: "Python",
        service_name: "demo-searxng",
        description: "SearXNG — privacy-friendly metasearch engine (Python)",
        display_name: "SearXNG",
        commit: "18af21159bd7b84880cd7265b184825493322232",
        pre_build: &[],
        prefetch_steps: &[
            "python3 -m pip download -r requirements.txt -d .ghost-cache/pip-wheelhouse --only-binary=:all: && touch .ghost-cache/pip-wheelhouse/.done",
        ],
        build_steps: &[
            "python3 -m venv --clear .venv",
            ".venv/bin/pip install --no-cache-dir -r requirements.txt",
        ],
        start_cmd: "{venv} -m searx.webapp",
        port: 8888,
        searxng: true,
        plugins: &[],
        tools: &["python3"],
        loopback_only: false,
    },
    DemoRecipe {
        owner: "usememos",
        name: "memos",
        language: "Go",
        service_name: "demo-memos",
        description: "Memos — self-hosted, open-source knowledge base (Go)",
        display_name: "Memos",
        commit: "245e5e3a3e95cd3648fd66a696e0970e5eef1254",
        pre_build: &[],
        // pnpm fetch fills the virtual store from the lockfile WITHOUT
        // building node_modules or running any lifecycle script; the sandboxed
        // install links node_modules from that warm store, offline.
        prefetch_steps: &["pnpm --dir web fetch --store-dir {project}/.ghost-cache/pnpm"],
        build_steps: &[
            // PrivateNetwork is enforced: --offline is required since the
            // store was filled by the prefetch (never reach the registry).
            "pnpm --dir web install --offline --store-dir {project}/.ghost-cache/pnpm",
            "pnpm --dir web release",
            "go build -o ghost-server ./cmd/memos",
        ],
        start_cmd: "{bin} --port {port}",
        port: 0,
        searxng: false,
        plugins: &[],
        tools: &["pnpm", "go"],
        loopback_only: false,
    },
];

/// Find a recipe by GitHub owner/name (case-insensitive).
pub fn find_recipe(owner: &str, name: &str) -> Option<&'static DemoRecipe> {
    DEMO_SERVICES
        .iter()
        .find(|r| r.owner.eq_ignore_ascii_case(owner) && r.name.eq_ignore_ascii_case(name))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn catalog_has_three_services() {
        assert_eq!(DEMO_SERVICES.len(), 3);
        assert_eq!(
            DEMO_SERVICES
                .iter()
                .map(|r| r.service_name)
                .collect::<Vec<_>>(),
            vec!["demo-vert", "demo-searxng", "demo-memos"]
        );
    }

    #[test]
    fn find_recipe_is_case_insensitive_and_total_for_catalog() {
        assert!(find_recipe("VERT-sh", "VERT").is_some());
        assert!(find_recipe("vert-sh", "vert").is_some());
        assert!(find_recipe("usememos", "memos").is_some());
        assert!(find_recipe("searxng", "searxng").is_some());
        assert!(find_recipe("foo", "bar").is_none());
    }

    /// Audit lesson: every build step's interpreter must be declared in
    /// `tools`, otherwise preflight passes while the build cannot run.
    #[test]
    fn every_declared_tool_covers_build_steps() {
        for r in DEMO_SERVICES {
            for step in r
                .pre_build
                .iter()
                .chain(r.build_steps.iter())
                .chain(r.prefetch_steps.iter())
            {
                let head = step.split_whitespace().next().unwrap();
                let base = head.rsplit('/').next().unwrap();
                if matches!(base, "bun" | "pnpm" | "go" | "python3") {
                    assert!(
                        r.tools.contains(&base),
                        "{}: build step '{step}' requires '{base}' but it is not in tools {:?}",
                        r.service_name,
                        r.tools
                    );
                }
            }
        }
    }

    /// Prefetch steps are downloaders, never executed in the sandbox; every
    /// one of them references a tool the recipe declares.
    #[test]
    fn prefetch_steps_are_downloaders_and_covered() {
        for r in DEMO_SERVICES {
            for step in r.prefetch_steps {
                let head = step.split_whitespace().next().unwrap();
                let base = head.rsplit('/').next().unwrap();
                assert!(
                    r.tools.contains(&base),
                    "{}: prefetch step '{step}' needs tool '{base}' not declared in {:?}",
                    r.service_name,
                    r.tools
                );
            }
        }
    }

    /// Only VERT may carry plugin pins (the others have none), and every pin
    /// must be a jsdelivr paraglide module whose cache filename matches the
    /// FNV1a-64 derivation and whose SHA-256 is well-formed hex. This is the
    /// guard that keeps a recipe plugin bump honest.
    #[test]
    fn plugin_pins_are_paraglide_jsdelivr_and_well_formed() {
        for r in DEMO_SERVICES {
            assert!(
                r.plugins.len() <= 2,
                "{}: unexpected plugin count {}",
                r.service_name,
                r.plugins.len()
            );
            if r.service_name != "demo-vert" {
                assert!(r.plugins.is_empty(), "{}: non-VERT plugin pins", r.service_name);
            }
            for (url, sha) in r.plugins {
                assert!(
                    url.starts_with("https://cdn.jsdelivr.net/npm/@inlang/plugin-"),
                    "{}: plugin URL must be a jsdelivr @inlang module: {url}",
                    r.service_name
                );
                assert_eq!(
                    sha.len(),
                    64,
                    "{}: plugin {url} SHA-256 must be 64 hex chars",
                    r.service_name
                );
                assert!(
                    sha.bytes().all(|b| b.is_ascii_hexdigit()),
                    "{}: plugin {url} SHA-256 is not hex",
                    r.service_name
                );
                // Cache filename must match the URL's FNV1a-64 base36 name.
                super::super::prefetch::paraglide_cache_name(url);
            }
        }
    }
}
