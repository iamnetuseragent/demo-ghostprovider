//! Supply-chain pin: the compiled-in remote allowlist must exactly match
//! what the in-code documentation (netlog.rs) declares. `cdn.jsdelivr.net`
//! was added for the SHA-pinned paraglide-js plugin fetch (VERT recipe);
//! README "Security model" is deliberately untouched in this change and
//! must be synced once the maintainer approves editing it. Any endpoint
//! change requires a conscious change here AND in netlog.rs.

use demo_ghostprovider::netlog::ALLOWED_ENDPOINTS;

#[test]
fn allowlist_matches_documented_endpoints() {
    let mut actual: Vec<_> = ALLOWED_ENDPOINTS.to_vec();
    actual.sort();
    assert_eq!(
        actual,
        vec![
            "api.github.com",
            "cdn.jsdelivr.net",
            "codeload.github.com",
            "github.com",
            "proxy.golang.org",
            "raw.githubusercontent.com",
            "storage.googleapis.com"
        ],
        "ALLOWED_ENDPOINTS changed! Update netlog.rs docs and this test in the \
         same commit, and justify it publicly. README 'Security model' sync is \
         pending by maintainer request."
    );
}

#[test]
fn local_endpoints_are_loopback_only() {
    for &host in demo_ghostprovider::netlog::LOCAL_ENDPOINTS {
        assert!(
            matches!(host, "127.0.0.1" | "localhost" | "[::1]"),
            "LOCAL_ENDPOINTS must only contain loopback hosts, found: {host}"
        );
    }
}
