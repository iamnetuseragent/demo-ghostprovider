//! GitHub URL parsing and repository metadata.

use serde::Deserialize;

use super::httpclient::get_text;

/// Matches `https://github.com/<owner>/<name>(.git)?/?` exactly. Plaintext
/// `http://` is refused: it would downgrade authentication and could never
/// pass the client's https-only gate anyway.
pub fn parse_github_url(url: &str) -> Option<(String, String)> {
    let rest = url.strip_prefix("https://")?;
    let rest = rest.strip_prefix("github.com/")?;

    // Reject anything with extra path segments, query or fragment parts.
    let mut segs = rest.split(['?', '#']);
    let path = segs.next()?;
    if segs.next().is_some() {
        return None;
    }
    let path = path.trim_end_matches('/');
    let mut parts = path.split('/');
    let owner = parts.next()?.trim();
    let name = parts.next()?.trim();
    if parts.next().is_some() || owner.is_empty() || name.is_empty() {
        return None;
    }
    let name = name.trim_end_matches(".git").trim_end_matches('/');
    if name.is_empty() || name.contains('/') {
        return None;
    }
    Some((owner.to_string(), name.to_string()))
}

#[derive(Debug, Deserialize)]
pub struct RepoMetadata {
    pub full_name: Option<String>,
    pub description: Option<String>,
    pub language: Option<String>,
    #[serde(rename = "stargazers_count")]
    pub stars: Option<u64>,
    #[serde(rename = "default_branch")]
    pub default_branch: Option<String>,
}

/// Fetch repo metadata from the GitHub API.
/// Errors mirror the Python version's user-facing categories.
pub fn fetch_repo_metadata(owner: &str, name: &str) -> Result<RepoMetadata, String> {
    let url = format!("https://api.github.com/repos/{owner}/{name}");
    match get_text(&url) {
        Ok(body) => serde_json::from_str::<RepoMetadata>(&body)
            .map_err(|e| format!("GitHub API returned malformed JSON: {e}")),
        Err(e) => {
            let msg = e.to_string();
            if msg.contains("HTTP 404") {
                Err("Repository does not exist or is private".into())
            } else if msg.contains("HTTP 403") {
                Err("GitHub API rate limit exceeded — try again later or use a token".into())
            } else if msg.contains("allowlist") {
                Err(msg)
            } else {
                Err(format!("Network error: {msg}"))
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_basic_urls() {
        assert_eq!(
            parse_github_url("https://github.com/VERT-sh/VERT"),
            Some(("VERT-sh".into(), "VERT".into()))
        );
        assert_eq!(
            parse_github_url("https://github.com/usememos/memos.git"),
            Some(("usememos".into(), "memos".into()))
        );
        assert_eq!(
            parse_github_url("https://github.com/searxng/searxng/"),
            Some(("searxng".into(), "searxng".into()))
        );
    }

    #[test]
    fn rejects_malformed_urls() {
        for url in [
            "http://github.com/a/b",
            "https://gitlab.com/a/b",
            "https://github.com/a/b/tree/main",
            "https://github.com/a",
            "github.com/a/b",
            "https://github.com//b",
            "https://github.com/a/",
            "",
        ] {
            assert_eq!(parse_github_url(url), None, "{url}");
        }
    }
}
