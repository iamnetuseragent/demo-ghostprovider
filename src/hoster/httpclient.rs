//! Allowlisted HTTP client.
//!
//! Every request goes through [`get_json`]/[`get_text`], which refuse to open
//! a socket to any host outside [`crate::netlog::ALLOWED_ENDPOINTS`]. All
//! attempts are recorded locally via `netlog::record` regardless of outcome.
//!
//! A `GITHUB_TOKEN`/`GH_TOKEN` from the environment is attached to
//! api.github.com calls only (avoids the 60 req/h anonymous limit). The token
//! is never logged, never written to disk.

use std::time::Duration;

use anyhow::{Context, anyhow};
use ureq::http::Response;

const TIMEOUT: Duration = Duration::from_secs(10);
const RETRIES: u32 = 2;

fn deny(url: &str) -> anyhow::Error {
    let host = host_of(url).unwrap_or_else(|| "<unparseable>".into());
    crate::netlog::record(
        &host,
        &path_of(url),
        Err("DENY: host not in allowlist".into()),
    );
    anyhow!("host '{host}' is not in the endpoint allowlist")
}

fn host_of(url: &str) -> Option<String> {
    let rest = url.split_once("://")?.1;
    let authority = rest.split(['/', '?', '#']).next()?;
    let host = authority.rsplit_once('@').map_or(authority, |(_, h)| h);
    let host = host.split(':').next().unwrap_or(host);
    Some(host.to_string())
}

fn path_of(url: &str) -> String {
    let rest = url.split_once("://").map(|(_, r)| r).unwrap_or(url);
    match rest.find('/') {
        Some(i) => {
            let p = &rest[i..];
            p.split(['?', '#']).next().unwrap_or("/").to_string()
        }
        None => "/".to_string(),
    }
}

/// Deny-by-default check used by every public entry point.
pub fn ensure_allowed(url: &str) -> anyhow::Result<()> {
    let host = host_of(url).ok_or_else(|| anyhow!("cannot parse URL: {url}"))?;
    if crate::netlog::is_allowed_host(&host) {
        Ok(())
    } else {
        Err(deny(url))
    }
}

fn github_token_for(url: &str) -> Option<String> {
    if url.contains("api.github.com") {
        std::env::var("GITHUB_TOKEN")
            .or_else(|_| std::env::var("GH_TOKEN"))
            .ok()
            .filter(|t| !t.is_empty())
    } else {
        None
    }
}

fn build_agent() -> ureq::Agent {
    ureq::Agent::config_builder()
        .timeout_global(Some(TIMEOUT))
        .user_agent(format!("demo-ghostprovider/{}", env!("CARGO_PKG_VERSION")))
        .build()
        .into()
}

fn attempt(agent: &ureq::Agent, url: &str) -> Result<Response<ureq::Body>, ureq::Error> {
    let mut req = agent.get(url);
    if let Some(token) = github_token_for(url) {
        req = req.header("Authorization", &format!("token {token}"));
    }
    // The GitHub REST media type is only valid on api.github.com; sending it
    // to plain github.com makes the server answer HTTP 406 (found live).
    if host_of(url).as_deref() == Some("api.github.com") {
        req = req.header("Accept", "application/vnd.github.v3+json");
    }
    req.call()
}

fn record_outcome(url: &str, res: &Result<Response<ureq::Body>, ureq::Error>) {
    let host = host_of(url).unwrap_or_default();
    let path = path_of(url);
    match res {
        Ok(r) => crate::netlog::record(&host, &path, Ok(r.status().as_u16())),
        Err(ureq::Error::StatusCode(code)) => {
            crate::netlog::record(&host, &path, Err(format!("HTTP {code}")))
        }
        Err(e) => crate::netlog::record(&host, &path, Err(e.to_string())),
    }
}

/// GET a URL and return the response body as text with retry/curl-fallback
/// semantics of the Python version reduced to: 1 retry after backoff on
/// connection errors. Status >= 400 is returned as an error carrying the code.
pub fn get_text(url: &str) -> anyhow::Result<String> {
    ensure_allowed(url)?;
    let agent = build_agent();
    let mut last_err: Option<anyhow::Error> = None;

    for attempt_no in 0..=RETRIES {
        let res = attempt(&agent, url);
        record_outcome(url, &res);

        match res {
            Ok(r) => {
                let status = r.status();
                let body = r.into_body().read_to_string().context("reading body")?;
                if status.as_u16() == 403 && attempt_no < RETRIES {
                    std::thread::sleep(Duration::from_secs(2 * (attempt_no as u64 + 1)));
                    continue;
                }
                return match status.as_u16() {
                    200..=299 => Ok(body),
                    code => Err(anyhow!("HTTP {code} from {url}")),
                };
            }
            Err(e) => {
                // HTTP error statuses are terminal; transport errors may retry.
                if matches!(e, ureq::Error::StatusCode(_)) {
                    return Err(anyhow!("{e}"));
                }
                last_err = Some(anyhow!("{e}"));
                if attempt_no < RETRIES {
                    std::thread::sleep(Duration::from_secs(1));
                }
            }
        }
    }
    Err(last_err.unwrap_or_else(|| anyhow!("request failed: {url}")))
}

/// GET a URL and parse JSON.
pub fn get_json<T: serde::de::DeserializeOwned>(url: &str) -> anyhow::Result<(u16, T)> {
    let text = get_text(url)?;
    let val = serde_json::from_str(&text).with_context(|| format!("parsing JSON from {url}"))?;
    Ok((200, val))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn denies_non_allowlisted_hosts_without_socket() {
        let err = get_text("https://evil.example/payload").unwrap_err();
        assert!(err.to_string().contains("allowlist"), "{err}");
    }

    #[test]
    fn denies_lookalike_hosts() {
        for url in [
            "https://github.com.evil.example/x",
            "https://api.github.com.evil.example/x",
            "https://raw.githubusercontent.com@127.0.0.1/x", // userinfo trick
            "notaurl",
        ] {
            assert!(ensure_allowed(url).is_err(), "{url} must be denied");
        }
    }

    #[test]
    fn accepts_allowlisted_hosts() {
        assert!(ensure_allowed("https://api.github.com/repos/a/b").is_ok());
        assert!(ensure_allowed("https://raw.githubusercontent.com/a/b/main/f").is_ok());
    }

    #[test]
    fn host_and_path_parsing() {
        assert_eq!(
            host_of("https://api.github.com/repos/a/b?x=1").as_deref(),
            Some("api.github.com")
        );
        assert_eq!(
            host_of("https://u:p@api.github.com/x").as_deref(),
            Some("api.github.com")
        );
        assert_eq!(
            path_of("https://api.github.com/repos/a/b?x=1"),
            "/repos/a/b"
        );
        assert_eq!(path_of("https://api.github.com"), "/");
    }

    #[test]
    fn token_only_for_api_host() {
        unsafe { std::env::set_var("GITHUB_TOKEN", "secret") };
        assert!(github_token_for("https://api.github.com/x").is_some());
        assert!(github_token_for("https://github.com/x").is_none());
        assert!(github_token_for("https://raw.githubusercontent.com/x/y").is_none());
        unsafe { std::env::remove_var("GITHUB_TOKEN") };
    }
}
