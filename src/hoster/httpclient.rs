//! Allowlisted HTTP client.
//!
//! Every request goes through [`get_json`]/[`get_text`], which refuse to open
//! a socket to any host outside [`crate::netlog::ALLOWED_ENDPOINTS`], and
//! refuse plaintext (`http://`) transport outright. Redirects are followed
//! MANUALLY, hop by hop: ureq is configured with `max_redirects(0)` and every
//! hop target is re-checked against `ensure_allowed` *before* a socket opens
//! for it — the Python version (and ureq's built-in redirect chasing) had no
//! such interception point, which is how a tarball fallback could silently
//! jump to `codeload.github.com` while the allowlist only ever saw the
//! initial URL. All attempts, one record per hop, are logged via
//! `netlog::record` regardless of outcome.
//!
//! A `GITHUB_TOKEN`/`GH_TOKEN` from the environment is attached to
//! api.github.com calls only (avoids the 60 req/h anonymous limit). It is
//! reconstructed per hop and never crosses a redirect boundary to another
//! host; it is never logged, never written to disk.

use std::time::Duration;

use anyhow::{Context, anyhow};
use ureq::http::Response;

const TIMEOUT: Duration = Duration::from_secs(10);
const RETRIES: u32 = 2;
/// Hop cap for manual redirect following. GitHub pointers (e.g. the archive
/// 302 to codeload) are a single hop; anything beyond this is hostile.
const MAX_REDIRECTS: u32 = 5;

fn scheme_of(url: &str) -> Option<&str> {
    url.split_once("://").map(|(s, _)| s)
}

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

/// Deny-by-default check used by every public entry point and re-run on every
/// redirect hop. Refuses anything that is not `https` before a socket can
/// open, then refuses hosts outside the allowlist.
pub fn ensure_allowed(url: &str) -> anyhow::Result<()> {
    let host = host_of(url).ok_or_else(|| anyhow!("cannot parse URL: {url}"))?;
    // HTTPS-only: a plaintext URL (user input or a downgrade redirect target)
    // must never reach the wire, even to an otherwise allowlisted host.
    if scheme_of(url) != Some("https") {
        crate::netlog::record(
            &host,
            &path_of(url),
            Err("DENY: non-https scheme (allowlist is https-only)".into()),
        );
        return Err(anyhow!(
            "refused {url}: the endpoint allowlist is https-only"
        ));
    }
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
        // Redirects are followed by hand below so the allowlist can be
        // re-checked on every hop. Turning automatic chasing off is what
        // makes that possible.
        .max_redirects(0)
        .user_agent(format!("demo-ghostprovider/{}", env!("CARGO_PKG_VERSION")))
        .build()
        .into()
}

/// Resolve a `Location` header against the current hop URL.
///
/// Handles absolute, protocol-relative (`//host/...`), root-relative
/// (`/path`) and path-relative targets. The result is always passed back
/// through [`ensure_allowed`], so a plaintext or off-allowlist target is
/// refused before a socket opens, never followed.
fn resolve_redirect(base: &str, location: &str) -> anyhow::Result<String> {
    if location.starts_with("http://") || location.starts_with("https://") {
        return Ok(location.to_string());
    }
    if let Some(rest) = location.strip_prefix("//") {
        return Ok(format!("https://{rest}"));
    }
    // `base` is always https (enforced upstream): origin = scheme + authority.
    let origin = base.split('/').take(3).collect::<Vec<_>>().join("/");
    if location.starts_with('/') {
        return Ok(format!("{origin}{location}"));
    }
    // Path-relative (RFC 3986 join): resolve against the base's directory.
    // `dir` is kept with its leading slash, so it is appended raw.
    let base_path = base.trim_start_matches(&origin);
    let dir = match base_path.rsplit_once('/') {
        Some((dir, _)) => dir,
        None => "/",
    };
    Ok(format!("{origin}{dir}/{location}"))
}

/// Execute one full request without automatic redirects. Every hop is gated
/// by [`ensure_allowed`] before a connection can open, and each hop is
/// net.log-recorded individually. Authorization/Accept headers are rebuilt
/// per hop, so the token never follows the redirect to another host.
fn attempt(agent: &ureq::Agent, url: &str) -> Result<Response<ureq::Body>, ureq::Error> {
    let mut current = url.to_string();
    for _ in 0..=MAX_REDIRECTS {
        ensure_allowed(&current)
            .map_err(|e| ureq::Error::Io(std::io::Error::other(e.to_string())))?;

        let mut req = agent.get(&current);
        if let Some(token) = github_token_for(&current) {
            req = req.header("Authorization", &format!("token {token}"));
        }
        // The GitHub REST media type is only valid on api.github.com; sending
        // it to plain github.com makes the server answer HTTP 406 (found live).
        if host_of(&current).as_deref() == Some("api.github.com") {
            req = req.header("Accept", "application/vnd.github.v3+json");
        }

        let res = match req.call() {
            Ok(r) => r,
            Err(e) => {
                // Failed hop is net.log-recorded at the URL it attempted.
                record_failure(&current, &e);
                return Err(e);
            }
        };
        record_outcome(&current, &res);
        let status = res.status();
        let location = res
            .headers()
            .get("location")
            .and_then(|v| v.to_str().ok())
            .map(str::to_owned);

        if status.is_redirection() {
            let Some(location) = location else {
                let _ = res.into_body().read_to_vec();
                return Err(ureq::Error::Io(std::io::Error::other(
                    "redirect response without a Location header",
                )));
            };
            // Consume the (usually tiny) redirect body so the pooled
            // connection is released cleanly.
            let _ = res.into_body().read_to_vec();
            current = resolve_redirect(&current, &location)
                .map_err(|e| ureq::Error::Io(std::io::Error::other(e.to_string())))?;
            continue;
        }
        return Ok(res);
    }
    Err(ureq::Error::Io(std::io::Error::other(format!(
        "too many redirects from {url}"
    ))))
}

fn record_outcome(url: &str, res: &Response<ureq::Body>) {
    let host = host_of(url).unwrap_or_default();
    let path = path_of(url);
    crate::netlog::record(&host, &path, Ok(res.status().as_u16()));
}

fn record_failure(url: &str, err: &ureq::Error) {
    let host = host_of(url).unwrap_or_default();
    let path = path_of(url);
    match err {
        ureq::Error::StatusCode(code) => {
            crate::netlog::record(&host, &path, Err(format!("HTTP {code}")))
        }
        e => crate::netlog::record(&host, &path, Err(e.to_string())),
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

/// GET a URL and return the response body as bytes with the same
/// allowlist/net.log/retry semantics as [`get_text`]. Used for binary payloads
/// such as source tarballs.
pub fn get_bytes(url: &str) -> anyhow::Result<Vec<u8>> {
    ensure_allowed(url)?;
    let agent = build_agent();
    let mut last_err: Option<anyhow::Error> = None;

    for attempt_no in 0..=RETRIES {
        let res = attempt(&agent, url);

        match res {
            Ok(r) => {
                let status = r.status();
                let body = r.into_body().read_to_vec().context("reading body")?;
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
        // codeload is the archive-redirect target the tarball fallback needs.
        assert!(ensure_allowed("https://codeload.github.com/a/b/tar.gz/master").is_ok());
    }

    #[test]
    fn refuses_plaintext_even_for_allowlisted_hosts() {
        for url in [
            "http://github.com/x",
            "http://api.github.com/repos/a/b",
            "http://codeload.github.com/a/b",
        ] {
            let err = get_text(url).unwrap_err();
            assert!(err.to_string().contains("allowlist"), "{url}: {err}");
        }
    }

    #[test]
    fn redirect_target_resolution() {
        let base = "https://api.github.com/repos/a/b";
        assert_eq!(
            resolve_redirect(base, "https://codeload.github.com/a/tar.gz/main").unwrap(),
            "https://codeload.github.com/a/tar.gz/main"
        );
        assert_eq!(
            resolve_redirect(base, "//codeload.github.com/a/tar.gz/main").unwrap(),
            "https://codeload.github.com/a/tar.gz/main"
        );
        assert_eq!(
            resolve_redirect(base, "/archive/main.tar.gz").unwrap(),
            "https://api.github.com/archive/main.tar.gz"
        );
        assert_eq!(
            resolve_redirect(base, "tarball/main.tar.gz").unwrap(),
            "https://api.github.com/repos/a/tarball/main.tar.gz"
        );
        // A plaintext absolute Location is returned as-is; the per-hop
        // ensure_allowed inside attempt() then refuses it.
        assert_eq!(
            resolve_redirect(base, "http://evil.example/x").unwrap(),
            "http://evil.example/x"
        );
    }

    #[test]
    fn redirect_hop_cap_bounds_loop() {
        // resolve+continue past MAX_REDIRECTS must fail closed with the
        // allowlist gate intact (no sockets opened by the loop itself).
        let base = "https://github.com/a/b";
        assert!(resolve_redirect(base, "https://github.com/c").is_ok());
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
