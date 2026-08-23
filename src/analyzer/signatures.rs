//! Well-known service fingerprints for HTTP probing.
//!
//! Ported from signatures.py without a regex engine: every signature is
//! either "substring appears inside <title>…</title>" or a plain
//! (optionally boundary-terminated) case-insensitive substring.

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Matcher {
    /// Case-insensitive substring within the first `<title>…</title>` block.
    TitleContains(&'static str),
    /// Case-insensitive substring anywhere in the body.
    Contains(&'static str),
    /// Substring followed by '/' or whitespace (e.g. `SearXNG/1.0`).
    WordSlash(&'static str),
}

#[derive(Debug, Clone, Copy)]
pub struct Signature {
    pub matcher: Matcher,
    pub service_type: &'static str,
    pub service_name: &'static str,
    pub confidence: u8,
}

const fn t(s: &'static str, ty: &'static str, name: &'static str, conf: u8) -> Signature {
    Signature {
        matcher: Matcher::TitleContains(s),
        service_type: ty,
        service_name: name,
        confidence: conf,
    }
}
const fn c(s: &'static str, ty: &'static str, name: &'static str, conf: u8) -> Signature {
    Signature {
        matcher: Matcher::Contains(s),
        service_type: ty,
        service_name: name,
        confidence: conf,
    }
}

pub const SERVICE_SIGNATURES: &[Signature] = &[
    // Search engines
    t("searxng", "search_engine", "SearXNG", 95),
    c("searxng", "search_engine", "SearXNG", 85),
    t("whoogle", "search_engine", "Whoogle Search", 95),
    c("whoogle", "search_engine", "Whoogle Search", 80),
    t("yacy", "search_engine", "YaCy", 95),
    t("shiori", "web_app", "Shiori", 90),
    // Media servers
    t("jellyfin", "media_server", "Jellyfin", 95),
    c("jellyfin", "media_server", "Jellyfin", 90),
    t("plex", "media_server", "Plex", 95),
    t("navidrome", "media_server", "Navidrome", 95),
    t("airsonic", "media_server", "Airsonic", 95),
    t("funkwhale", "media_server", "Funkwhale", 95),
    t("koel", "media_server", "Koel", 95),
    t("black candy", "media_server", "Black Candy", 95),
    t("sonarr", "media_server", "Sonarr", 95),
    t("radarr", "media_server", "Radarr", 95),
    t("sabnzbd", "media_server", "SABnzbd", 95),
    t("transmission", "media_server", "Transmission", 95),
    t("qbittorrent", "media_server", "qBittorrent", 95),
    // Dashboards & monitoring
    t("home assistant", "dashboard", "Home Assistant", 95),
    t("home-assistant", "dashboard", "Home Assistant", 95),
    t("grafana", "dashboard", "Grafana", 95),
    c("grafana", "dashboard", "Grafana", 80),
    t("prometheus", "dashboard", "Prometheus", 95),
    t("netdata", "dashboard", "Netdata", 95),
    t("portainer", "dashboard", "Portainer", 95),
    t("phpmyadmin", "dashboard", "phpMyAdmin", 90),
    // Web servers / proxies (header-based checks live in probe.rs)
    t("caddy", "proxy", "Caddy", 80),
    t("traefik", "proxy", "Traefik", 80),
    // Dev servers / tools
    t("phpinfo", "dev_server", "PHP info", 95),
    c("vite", "dev_server", "Vite Dev Server", 80),
    c("webpack", "dev_server", "Webpack Dev Server", 80),
    // File sharing
    t("filegator", "file_server", "FileGator", 95),
    t("nextcloud", "file_server", "Nextcloud", 95),
    t("owncloud", "file_server", "ownCloud", 95),
    // RSS readers
    t("miniflux", "web_app", "Miniflux", 95),
    t("tiny tiny rss", "web_app", "Tiny Tiny RSS", 95),
    t("freshrss", "web_app", "FreshRSS", 95),
    // Password managers
    t("bitwarden", "web_app", "Bitwarden", 95),
    t("vaultwarden", "web_app", "Vaultwarden", 95),
    // Git services
    t("gitea", "web_app", "Gitea", 95),
    t("gitlab", "web_app", "GitLab", 95),
    t("gogs", "web_app", "Gogs", 95),
    // Note-taking / wikis
    t("outline", "web_app", "Outline", 85),
    t("bookstack", "web_app", "BookStack", 95),
    t("wiki.js", "web_app", "Wiki.js", 95),
    // Generic services
    t("syncthing", "web_app", "Syncthing", 95),
    c("syncthing", "web_app", "Syncthing", 80),
    t("adguard", "proxy", "AdGuard Home", 95),
    t("pi-hole", "proxy", "Pi-hole", 95),
    t("pihole", "proxy", "Pi-hole", 95),
    t("uptime kuma", "dashboard", "Uptime Kuma", 95),
    t("changedetection", "web_app", "Changedetection.io", 95),
    // DB admin tools
    t("adminer", "dashboard", "Adminer", 90),
    t("pgadmin", "dashboard", "pgAdmin", 90),
    // System services (non-hostable)
    t("router", "system_service", "Router Admin", 70),
    t("printer", "system_service", "Printer Interface", 70),
    // Desktop/GUI apps with web UI
    t("jupyter", "desktop_app", "Jupyter Notebook", 80),
];

/// Apply all signatures to a response body; returns the best match.
pub fn match_body(body: &str) -> Option<&'static Signature> {
    let lower = body.to_lowercase();
    let title = extract_title(&lower);

    let mut best: Option<&Signature> = None;
    for sig in SERVICE_SIGNATURES {
        let hit = match sig.matcher {
            Matcher::TitleContains(needle) => title.contains(needle),
            Matcher::Contains(needle) => lower.contains(needle),
            Matcher::WordSlash(needle) => word_slash_hit(&lower, needle),
        };
        if hit && best.is_none_or(|b| sig.confidence > b.confidence) {
            best = Some(sig);
        }
    }
    best
}

fn extract_title(lower_body: &str) -> String {
    let start = match lower_body.find("<title") {
        Some(i) => match lower_body[i..].find('>') {
            Some(j) => i + j + 1,
            None => return String::new(),
        },
        None => return String::new(),
    };
    match lower_body[start..].find("</title>") {
        Some(end) => lower_body[start..start + end].to_string(),
        None => String::new(),
    }
}

fn word_slash_hit(haystack: &str, needle: &str) -> bool {
    let mut from = 0;
    while let Some(pos) = haystack[from..].find(needle) {
        let abs = from + pos;
        let after = haystack[abs + needle.len()..].chars().next();
        if matches!(after, Some('/') | Some(' ') | Some('\t') | Some('\n')) {
            return true;
        }
        from = abs + needle.len();
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn searxng_by_title() {
        let body = "<html><head><title>SearXNG — metasearch</title></head></html>";
        let sig = match_body(body).unwrap();
        assert_eq!(sig.service_name, "SearXNG");
        assert_eq!(sig.confidence, 95);
    }

    #[test]
    fn grafana_by_substring() {
        assert_eq!(
            match_body("welcome to GRAFANA dashboard")
                .unwrap()
                .service_name,
            "Grafana"
        );
    }

    #[test]
    fn no_false_positive_on_unrelated() {
        assert!(match_body("<h1>Hello world</h1>").is_none());
    }

    #[test]
    fn word_slash_boundary() {
        assert!(match_body("powered by SearXNG/version").is_some());
    }
}
