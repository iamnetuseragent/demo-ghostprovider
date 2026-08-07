"""Well-known service fingerprints for HTTP probing."""

import re

SERVICE_SIGNATURES: list[tuple[re.Pattern, str, str, int]] = [
    # Search engines
    (re.compile(rb"<title>.*SearXNG?.*</title>", re.IGNORECASE), "search_engine", "SearXNG", 95),
    (re.compile(rb"SearXNG?[/\s]", re.IGNORECASE), "search_engine", "SearXNG", 85),
    (re.compile(rb"<title>.*Whoogle.*</title>", re.IGNORECASE), "search_engine", "Whoogle Search", 95),
    (re.compile(rb"Whoogle", re.IGNORECASE), "search_engine", "Whoogle Search", 80),
    (re.compile(rb"<title>.*YaCy.*</title>", re.IGNORECASE), "search_engine", "YaCy", 95),
    (re.compile(rb"libre(y|Y)ou", re.IGNORECASE), "search_engine", "LibreY", 90),
    (re.compile(rb"<title>.*Shiori.*</title>", re.IGNORECASE), "search_engine", "Shiori", 90),

    # Media servers
    (re.compile(rb"<title>.*Jellyfin.*</title>", re.IGNORECASE), "media_server", "Jellyfin", 95),
    (re.compile(rb"Jellyfin[/\s]", re.IGNORECASE), "media_server", "Jellyfin", 90),
    (re.compile(rb"<title>.*Plex.*</title>", re.IGNORECASE), "media_server", "Plex", 95),
    (re.compile(rb"<title>.*Navidrome.*</title>", re.IGNORECASE), "media_server", "Navidrome", 95),
    (re.compile(rb"<title>.*Airsonic.*</title>", re.IGNORECASE), "media_server", "Airsonic", 95),
    (re.compile(rb"<title>.*Funkwhale.*</title>", re.IGNORECASE), "media_server", "Funkwhale", 95),
    (re.compile(rb"<title>.*Koel.*</title>", re.IGNORECASE), "media_server", "Koel", 95),
    (re.compile(rb"<title>.*Black Candy.*</title>", re.IGNORECASE), "media_server", "Black Candy", 95),
    (re.compile(rb"<title>.*Sonarr.*</title>", re.IGNORECASE), "media_server", "Sonarr", 95),
    (re.compile(rb"<title>.*Radarr.*</title>", re.IGNORECASE), "media_server", "Radarr", 95),
    (re.compile(rb"<title>.*SABnzbd.*</title>", re.IGNORECASE), "media_server", "SABnzbd", 95),
    (re.compile(rb"<title>.*Transmission.*</title>", re.IGNORECASE), "media_server", "Transmission", 95),
    (re.compile(rb"<title>.*qBittorrent.*</title>", re.IGNORECASE), "media_server", "qBittorrent", 95),

    # Dashboards & monitoring
    (re.compile(rb"<title>.*Home[ -]?[Aa]ssistant.*</title>", re.IGNORECASE), "dashboard", "Home Assistant", 95),
    (re.compile(rb"<title>.*Grafana.*</title>", re.IGNORECASE), "dashboard", "Grafana", 95),
    (re.compile(rb"grafana", re.IGNORECASE), "dashboard", "Grafana", 80),
    (re.compile(rb"<title>.*Prometheus.*</title>", re.IGNORECASE), "dashboard", "Prometheus", 95),
    (re.compile(rb"<title>.*Netdata.*</title>", re.IGNORECASE), "dashboard", "Netdata", 95),
    (re.compile(rb"<title>.*Portainer.*</title>", re.IGNORECASE), "dashboard", "Portainer", 95),
    (re.compile(rb"<title>.*phpMyAdmin.*</title>", re.IGNORECASE), "dashboard", "phpMyAdmin", 90),

    # Web servers / proxies
    (re.compile(rb"nginx[/\s]", re.IGNORECASE), "proxy", "Nginx", 80),
    (re.compile(rb"Apache[/\s]", re.IGNORECASE), "web_app", "Apache HTTPD", 80),
    (re.compile(rb"Caddy[/\s]", re.IGNORECASE), "proxy", "Caddy", 80),
    (re.compile(rb"Traefik", re.IGNORECASE), "proxy", "Traefik", 80),

    # Dev servers / tools
    (re.compile(rb"<title>.*phpinfo.*</title>", re.IGNORECASE), "dev_server", "PHP info", 95),
    (re.compile(rb"Vite|vite", re.IGNORECASE), "dev_server", "Vite Dev Server", 80),
    (re.compile(rb"webpack", re.IGNORECASE), "dev_server", "Webpack Dev Server", 80),

    # File sharing
    (re.compile(rb"<title>.*File[Gg]ator.*</title>", re.IGNORECASE), "file_server", "FileGator", 95),
    (re.compile(rb"<title>.*Nextcloud.*</title>", re.IGNORECASE), "file_server", "Nextcloud", 95),
    (re.compile(rb"<title>.*OwnCloud.*</title>", re.IGNORECASE), "file_server", "ownCloud", 95),

    # RSS readers
    (re.compile(rb"<title>.*Miniflux.*</title>", re.IGNORECASE), "web_app", "Miniflux", 95),
    (re.compile(rb"<title>.*Tiny[ -]?Tiny[ -]?RSS.*</title>", re.IGNORECASE), "web_app", "Tiny Tiny RSS", 95),
    (re.compile(rb"<title>.*FreshRSS.*</title>", re.IGNORECASE), "web_app", "FreshRSS", 95),

    # Password managers
    (re.compile(rb"<title>.*Bitwarden.*</title>", re.IGNORECASE), "web_app", "Bitwarden", 95),
    (re.compile(rb"<title>.*Vaultwarden.*</title>", re.IGNORECASE), "web_app", "Vaultwarden", 95),

    # Git services
    (re.compile(rb"<title>.*Gitea.*</title>", re.IGNORECASE), "web_app", "Gitea", 95),
    (re.compile(rb"<title>.*GitLab.*</title>", re.IGNORECASE), "web_app", "GitLab", 95),
    (re.compile(rb"<title>.*Gogs.*</title>", re.IGNORECASE), "web_app", "Gogs", 95),

    # Note-taking / wikis
    (re.compile(rb"<title>.*Outline.*</title>", re.IGNORECASE), "web_app", "Outline", 85),
    (re.compile(rb"<title>.*Bookstack.*</title>", re.IGNORECASE), "web_app", "BookStack", 95),
    (re.compile(rb"<title>.*Wiki\.js.*</title>", re.IGNORECASE), "web_app", "Wiki.js", 95),
    (re.compile(rb"<title>.*Docum?ent.*</title>", re.IGNORECASE), "web_app", "Documenso", 80),

    # Generic services
    (re.compile(rb"<title>.*Syncthing.*</title>", re.IGNORECASE), "web_app", "Syncthing", 95),
    (re.compile(rb"syncthing", re.IGNORECASE), "web_app", "Syncthing", 80),
    (re.compile(rb"<title>.*AdGuard.*</title>", re.IGNORECASE), "proxy", "AdGuard Home", 95),
    (re.compile(rb"<title>.*Pi-?hole.*</title>", re.IGNORECASE), "proxy", "Pi-hole", 95),
    (re.compile(rb"<title>.*Uptime[ -]?[Kk]uma.*</title>", re.IGNORECASE), "dashboard", "Uptime Kuma", 95),
    (re.compile(rb"<title>.*Changedetection.*</title>", re.IGNORECASE), "web_app", "Changedetection.io", 95),

    # DB admin tools
    (re.compile(rb"<title>.*Adminer.*</title>", re.IGNORECASE), "dashboard", "Adminer", 90),
    (re.compile(rb"<title>.*PgAdmin.*</title>", re.IGNORECASE), "dashboard", "pgAdmin", 90),

    # System services (non-hostable)
    (re.compile(rb"<title>.*Router.*</title>", re.IGNORECASE), "system_service", "Router Admin", 70),
    (re.compile(rb"<title>.*Printer.*</title>", re.IGNORECASE), "system_service", "Printer Interface", 70),

    # Desktop/GUI apps with web UI
    (re.compile(rb"<title>.*Jupyter.*</title>", re.IGNORECASE), "desktop_app", "Jupyter Notebook", 80),
]
