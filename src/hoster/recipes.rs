//! Curated demo catalog — exactly three supported services.
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
    pub pre_build: &'static [&'static str],
    pub build_steps: &'static [&'static str],
    /// Placeholders: {bin} {venv} {python} {project} {port} {self}
    /// {self} expands to this binary — used by the built-in static server.
    pub start_cmd: &'static str,
    /// 0 = pick a random free port.
    pub port: u16,
    pub searxng: bool,
    /// Executables required on PATH for the build steps to succeed.
    pub tools: &'static [&'static str],
}

pub const DEMO_SERVICES: &[DemoRecipe] = &[
    DemoRecipe {
        owner: "VERT-sh",
        name: "VERT",
        language: "JavaScript",
        service_name: "demo-vert",
        description: "VERT — next-generation file converter (Svelte)",
        display_name: "VERT",
        pre_build: &["if [ -f .env.example ] && [ ! -f .env ]; then cp .env.example .env; fi"],
        build_steps: &["bun install", "bun run build"],
        // Served by THIS binary (built-in static server) instead of shelling
        // out to `python -m http.server`: one less host dependency.
        start_cmd: "{self} __serve-static {project}/build {port}",
        port: 0,
        searxng: false,
        tools: &["bun"],
    },
    DemoRecipe {
        owner: "searxng",
        name: "searxng",
        language: "Python",
        service_name: "demo-searxng",
        description: "SearXNG — privacy-friendly metasearch engine (Python)",
        display_name: "SearXNG",
        pre_build: &[],
        build_steps: &[
            "python3 -m venv --clear .venv",
            ".venv/bin/pip install --no-cache-dir -r requirements.txt",
        ],
        start_cmd: "{venv} -m searx.webapp",
        port: 8888,
        searxng: true,
        tools: &["python3"],
    },
    DemoRecipe {
        owner: "usememos",
        name: "memos",
        language: "Go",
        service_name: "demo-memos",
        description: "Memos — self-hosted, open-source knowledge base (Go)",
        display_name: "Memos",
        pre_build: &[],
        build_steps: &[
            "pnpm --dir web install",
            "pnpm --dir web release",
            "go build -o ghost-server ./cmd/memos",
        ],
        start_cmd: "{bin} --port {port}",
        port: 0,
        searxng: false,
        tools: &["pnpm", "go"],
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
    fn catalog_has_exactly_three_services() {
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
        assert!(find_recipe("foo", "bar").is_none());
    }

    /// Audit lesson: every build step's interpreter must be declared in
    /// `tools`, otherwise preflight passes while the build cannot run.
    #[test]
    fn every_declared_tool_covers_build_steps() {
        for r in DEMO_SERVICES {
            for step in r.pre_build.iter().chain(r.build_steps.iter()) {
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
}
