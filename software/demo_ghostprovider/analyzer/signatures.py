"""Well-known service fingerprints for HTTP probing."""

import re

SERVICE_SIGNATURES: list[tuple[re.Pattern, str, str, int]] = [
    # Search engines
    (re.compile(rb"<title>.*SearXNG?.*</title>", re.I), "search_engine", "SearXNG", 95),
    (re.compile(rb"SearXNG?[/\s]", re.I), "search_engine", "SearXNG", 85),
    (re.compile(rb"<title>.*Whoogle.*</title>", re.I), "search_engine", "Whoogle Search", 95),
    (re.compile(rb"Whoogle", re.I), "search_engine", "Whoogle Search", 80),
    (re.compile(rb"<title>.*YaCy.*</title>", re.I), "search_engine", "YaCy", 95),
    (re.compile(rb"libre(y|Y)ou", re.I), "search_engine", "LibreY", 90),
    (re.compile(rb"<title>.*Shiori.*</title>", re.I), "search_engine", "Shiori", 90),

    # Media servers
    (re.compile(rb"<title>.*Jellyfin.*</title>", re.I), "media_server", "Jellyfin", 95),
    (re.compile(rb"Jellyfin[/\s]", re.I), "media_server", "Jellyfin", 90),
    (re.compile(rb"<title>.*Plex.*</title>", re.I), "media_server", "Plex", 95),
    (re.compile(rb"<title>.*Navidrome.*</title>", re.I), "media_server", "Navidrome", 95),
    (re.compile(rb"<title>.*Airsonic.*</title>", re.I), "media_server", "Airsonic", 95),
    (re.compile(rb"<title>.*Funkwhale.*</title>", re.I), "media_server", "Funkwhale", 95),
    (re.compile(rb"<title>.*Koel.*</title>", re.I), "media_server", "Koel", 95),
    (re.compile(rb"<title>.*Black Candy.*</title>", re.I), "media_server", "Black Candy", 95),
    (re.compile(rb"<title>.*Sonarr.*</title>", re.I), "media_server", "Sonarr", 95),
    (re.compile(rb"<title>.*Radarr.*</title>", re.I), "media_server", "Radarr", 95),
    (re.compile(rb"<title>.*SABnzbd.*</title>", re.I), "media_server", "SABnzbd", 95),
    (re.compile(rb"<title>.*Transmission.*</title>", re.I), "media_server", "Transmission", 95),
    (re.compile(rb"<title>.*qBittorrent.*</title>", re.I), "media_server", "qBittorrent", 95),

    # Dashboards & monitoring
    (re.compile(rb"<title>.*Home[ -]?[Aa]ssistant.*</title>", re.I), "dashboard", "Home Assistant", 95),
    (re.compile(rb"<title>.*Grafana.*</title>", re.I), "dashboard", "Grafana", 95),
    (re.compile(rb"grafana", re.I), "dashboard", "Grafana", 80),
    (re.compile(rb"<title>.*Prometheus.*</title>", re.I), "dashboard", "Prometheus", 95),
    (re.compile(rb"<title>.*Netdata.*</title>", re.I), "dashboard", "Netdata", 95),
    (re.compile(rb"<title>.*Portainer.*</title>", re.I), "dashboard", "Portainer", 95),
    (re.compile(rb"<title>.*phpMyAdmin.*</title>", re.I), "dashboard", "phpMyAdmin", 90),

    # Web servers / proxies
    (re.compile(rb"nginx[/\s]", re.I), "proxy", "Nginx", 80),
    (re.compile(rb"Apache[/\s]", re.I), "web_app", "Apache HTTPD", 80),
    (re.compile(rb"Caddy[/\s]", re.I), "proxy", "Caddy", 80),
    (re.compile(rb"Traefik", re.I), "proxy", "Traefik", 80),

    # Dev servers / tools
    (re.compile(rb"<title>.*phpinfo.*</title>", re.I), "dev_server", "PHP info", 95),
    (re.compile(rb"Vite|vite", re.I), "dev_server", "Vite Dev Server", 80),
    (re.compile(rb"webpack", re.I), "dev_server", "Webpack Dev Server", 80),

    # File sharing
    (re.compile(rb"<title>.*File[Gg]ator.*</title>", re.I), "file_server", "FileGator", 95),
    (re.compile(rb"<title>.*Nextcloud.*</title>", re.I), "file_server", "Nextcloud", 95),
    (re.compile(rb"<title>.*OwnCloud.*</title>", re.I), "file_server", "ownCloud", 95),

    # RSS readers
    (re.compile(rb"<title>.*Miniflux.*</title>", re.I), "web_app", "Miniflux", 95),
    (re.compile(rb"<title>.*Tiny[ -]?Tiny[ -]?RSS.*</title>", re.I), "web_app", "Tiny Tiny RSS", 95),
    (re.compile(rb"<title>.*FreshRSS.*</title>", re.I), "web_app", "FreshRSS", 95),

    # Password managers
    (re.compile(rb"<title>.*Bitwarden.*</title>", re.I), "web_app", "Bitwarden", 95),
    (re.compile(rb"<title>.*Vaultwarden.*</title>", re.I), "web_app", "Vaultwarden", 95),

    # Git services
    (re.compile(rb"<title>.*Gitea.*</title>", re.I), "web_app", "Gitea", 95),
    (re.compile(rb"<title>.*GitLab.*</title>", re.I), "web_app", "GitLab", 95),
    (re.compile(rb"<title>.*Gogs.*</title>", re.I), "web_app", "Gogs", 95),

    # Note-taking / wikis
    (re.compile(rb"<title>.*Outline.*</title>", re.I), "web_app", "Outline", 85),
    (re.compile(rb"<title>.*Bookstack.*</title>", re.I), "web_app", "BookStack", 95),
    (re.compile(rb"<title>.*Wiki\.js.*</title>", re.I), "web_app", "Wiki.js", 95),
    (re.compile(rb"<title>.*Docum?ent.*</title>", re.I), "web_app", "Documenso", 80),

    # Generic services
    (re.compile(rb"<title>.*Syncthing.*</title>", re.I), "web_app", "Syncthing", 95),
    (re.compile(rb"syncthing", re.I), "web_app", "Syncthing", 80),
    (re.compile(rb"<title>.*AdGuard.*</title>", re.I), "proxy", "AdGuard Home", 95),
    (re.compile(rb"<title>.*Pi-?hole.*</title>", re.I), "proxy", "Pi-hole", 95),
    (re.compile(rb"<title>.*Uptime[ -]?[Kk]uma.*</title>", re.I), "dashboard", "Uptime Kuma", 95),
    (re.compile(rb"<title>.*Changedetection.*</title>", re.I), "web_app", "Changedetection.io", 95),

    # DB admin tools
    (re.compile(rb"<title>.*Adminer.*</title>", re.I), "dashboard", "Adminer", 90),
    (re.compile(rb"<title>.*PgAdmin.*</title>", re.I), "dashboard", "pgAdmin", 90),

    # System services (non-hostable)
    (re.compile(rb"<title>.*Router.*</title>", re.I), "system_service", "Router Admin", 70),
    (re.compile(rb"<title>.*Printer.*</title>", re.I), "system_service", "Printer Interface", 70),

    # Desktop/GUI apps with web UI
    (re.compile(rb"<title>.*Jupyter.*</title>", re.I), "desktop_app", "Jupyter Notebook", 80),
]
