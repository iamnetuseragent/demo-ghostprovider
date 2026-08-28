//! Supply-chain pin: the compiled-in remote allowlist must exactly match
//! what README.md documents. Any new endpoint requires a conscious change
//! here AND in the README transparency table.

use demo_ghostprovider::netlog::ALLOWED_ENDPOINTS;

#[test]
fn allowlist_matches_documented_endpoints() {
    let mut actual: Vec<_> = ALLOWED_ENDPOINTS.to_vec();
    actual.sort();
    assert_eq!(
        actual,
        vec![
            "api.github.com",
            "codeload.github.com",
            "github.com",
            "raw.githubusercontent.com"
        ],
        "ALLOWED_ENDPOINTS changed! Update README.md (Transparency section) \
         and this test in the same commit, and justify it publicly."
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
