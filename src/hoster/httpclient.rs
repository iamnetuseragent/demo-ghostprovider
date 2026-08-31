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
/// Budget for a whole large body (source tarballs). Slow-but-alive links
/// (e.g. a rate-limited path to codeload at ~8KB/s) legitimately need tens of
/// minutes; this only caps the absolute worst case. Truly-dead transfers are
/// caught much sooner by [`SLOW_BODY_IDLE`] below.
const SLOW_TIMEOUT: Duration = Duration::from_secs(60 * 90);
/// A single body read (or pause between chunks) may block for at most this
/// long on the slow path. A trickling peer keeps individual reads short;
/// a socket that stopped moving data entirely aborts after one idle window
/// instead of hanging for [`SLOW_TIMEOUT`].
const SLOW_BODY_IDLE: Duration = Duration::from_secs(45);

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

fn build_agent(global: Duration, recv_body: Option<Duration>) -> ureq::Agent {
    build_agent_named_encoding(global, recv_body, None)
}

/// Variant that lets the caller stop advertising gzip. Needed for byte-range
/// requests: a compressed slice cannot be inflated, and ureq would try (and
/// fail with "unexpected end of file") whenever the server gzips the body.
fn build_agent_identity(global: Duration, recv_body: Option<Duration>) -> ureq::Agent {
    build_agent_named_encoding(global, recv_body, Some(ureq::config::AutoHeaderValue::None))
}

fn build_agent_named_encoding(
    global: Duration,
    recv_body: Option<Duration>,
    accept_encoding: Option<ureq::config::AutoHeaderValue>,
) -> ureq::Agent {
    let mut b = ureq::Agent::config_builder()
        // Redirects are followed by hand below so the allowlist can be
        // re-checked on every hop. Turning automatic chasing off is what
        // makes that possible.
        .max_redirects(0)
        .user_agent(format!("demo-ghostprovider/{}", env!("CARGO_PKG_VERSION")));
    if let Some(t) = recv_body {
        b = b.timeout_recv_body(Some(t));
    }
    if let Some(enc) = accept_encoding {
        b = b.accept_encoding(enc);
    }
    b.timeout_global(Some(global)).build().into()
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
    attempt_impl(agent, url, None)
}

/// Like [`attempt`] but sends a `Range` header for segment-wise downloads of
/// large blobs through the same allowlist/redirect machinery.
fn attempt_range(
    agent: &ureq::Agent,
    url: &str,
    range: (u64, u64),
) -> Result<Response<ureq::Body>, ureq::Error> {
    attempt_impl(agent, url, Some(range))
}

fn attempt_impl(
    agent: &ureq::Agent,
    url: &str,
    range: Option<(u64, u64)>,
) -> Result<Response<ureq::Body>, ureq::Error> {
    let mut current = url.to_string();
    for _ in 0..=MAX_REDIRECTS {
        ensure_allowed(&current)
            .map_err(|e| ureq::Error::Io(std::io::Error::other(e.to_string())))?;

        let mut req = agent.get(&current);
        if let Some((start, end)) = range {
            req = req.header("Range", &format!("bytes={start}-{end}"));
        }
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

/// Total length of a range-addressable blob without transferring it. Sends a
/// `Range: bytes=0-0` probe under identity encoding (so a gzip decision at
/// the proxy cannot skew the reported size) and reads the size from a
/// standard range-response header. Traverses the same [`attempt_range`]
/// path, so every hop is allowlist-gated and net.log-recorded.
pub fn remote_len(url: &str) -> anyhow::Result<u64> {
    let agent = build_agent_identity(Duration::from_secs(30), Some(Duration::from_secs(60)));
    let res = attempt_range(&agent, url, (0, 0)).map_err(|e| anyhow::anyhow!("{e}"))?;
    let status = res.status().as_u16();
    if status >= 400 {
        anyhow::bail!("probe failed: HTTP {status} for {url}");
    }
    let hdrs = res.headers();
    // On a 206 the `content-length` equals the *range* length (here: 1), while
    // `content-range` carries the real total — so it must win. storage loops
    // additionally expose the canonical size via x-goog-stored-content-length;
    // a plain 200 (Range ignored) is served with an honest content-length.
    for key in ["content-range", "x-goog-stored-content-length", "content-length"] {
        let Some(v) = hdrs.get(key) else {
            continue;
        };
        let Ok(s) = v.to_str() else {
            continue;
        };
        let parsed = if key == "content-range" {
            // "bytes 0-0/TOTAL"
            s.rsplit_once('/').and_then(|(_, total)| total.trim().parse::<u64>().ok())
        } else {
            s.trim().parse::<u64>().ok()
        };
        if let Some(n) = parsed {
            if n > 0 {
                return Ok(n);
            }
        }
    }
    anyhow::bail!("no usable size header in probe response for {url}")
}

/// GET a URL and return the response body as text with retry/curl-fallback
/// semantics of the Python version reduced to: 1 retry after backoff on
/// connection errors. Status >= 400 is returned as an error carrying the code.
pub fn get_text(url: &str) -> anyhow::Result<String> {
    ensure_allowed(url)?;
    let agent = build_agent(TIMEOUT, None);
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
    let agent = build_agent(TIMEOUT, None);
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

/// GET a large body (source tarball) over a slow-but-alive link. The global
/// budget is [`SLOW_TIMEOUT`] and a single idle read may take up to
/// [`SLOW_BODY_IDLE`] — so a rate-limited transfer slowly crawling along is
/// allowed to finish, while a socket that truly stopped moving data aborts in
/// one idle window instead of hanging for the whole budget.
pub fn get_bytes_slow(url: &str) -> anyhow::Result<Vec<u8>> {
    ensure_allowed(url)?;
    let agent = build_agent(SLOW_TIMEOUT, Some(SLOW_BODY_IDLE));
    let mut last_err: Option<anyhow::Error> = None;

    for attempt_no in 0..=RETRIES {
        let res = attempt(&agent, url);

        match res {
            Ok(r) => {
                let status = r.status();
                let body = r.into_body().read_to_vec().context("reading slow body")?;
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

/// GET a byte range (`start..=end`, inclusive) of a large body over a
/// slow-but-alive link. Used to shard one huge blob (e.g. a 52MB embedded
/// wasm) into parallel flows whose aggregate clears a per-flow transfer cap.
/// The request carries a `Range` header and returns exactly the requested
/// span; if the server ignores Range (HTTP 200 full body), the caller's
/// length check catches it and treats the fetch as failed. Same budgets and
/// hop-by-hop allowlist gating as [`get_bytes_slow`].
pub fn get_bytes_range(url: &str, start: u64, end: u64) -> anyhow::Result<Vec<u8>> {
    ensure_allowed(url)?;
    let agent = build_agent_identity(SLOW_TIMEOUT, Some(SLOW_BODY_IDLE));
    let mut last_err: Option<anyhow::Error> = None;

    for attempt_no in 0..=RETRIES {
        let res = attempt_range(&agent, url, (start, end));

        match res {
            Ok(r) => {
                let status = r.status();
                let body = r.into_body().read_to_vec().context("reading ranged body")?;
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

/// Probe an allowlisted host with an almost-bodyless HEAD request. Used by
/// preflight where a full GET (e.g. the github.com homepage) can be tens of
/// kilobytes — legitimately slow on a throttled link — and would falsely
/// report "unreachable". Redirects are followed hop-by-hop through the same
/// allowlist gate as any other request; success means HTTPS + host answered.
pub fn head_ok(url: &str) -> bool {
    ensure_allowed(url).is_ok_and(|_| {
        let agent = build_agent(TIMEOUT, None);
        let mut current = url.to_string();
        for _ in 0..=MAX_REDIRECTS {
            let res = match agent.head(&current).call() {
                Ok(r) => r,
                Err(e) => {
                    record_failure(&current, &e);
                    return false;
                }
            };
            record_outcome(&current, &res);
            if res.status().is_redirection() {
                let Some(location) = res
                    .headers()
                    .get("location")
                    .and_then(|v| v.to_str().ok())
                    .map(str::to_owned)
                else {
                    let _ = res.into_body().read_to_vec();
                    return false;
                };
                let _ = res.into_body().read_to_vec();
                current = match resolve_redirect(&current, &location) {
                    Ok(next) => next,
                    Err(_) => return false,
                };
                if ensure_allowed(&current).is_err() {
                    return false;
                }
                continue;
            }
            return res.status().is_success();
        }
        false
    })
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
