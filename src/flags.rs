//! Truthy boolean environment-flag parsing.
//!
//! The remaining opt-out flag (`GHOSTPROVIDER_NO_NETLOG`) accepts a small set
//! of truthy values, matched case-insensitively. The build sandbox has no
//! opt-out — see `hoster::sandbox`.

/// True when `name` is set to `1`, `true`, `yes` or `on`.
pub fn env_flag(name: &str) -> bool {
    std::env::var(name)
        .map(|v| matches!(v.to_ascii_lowercase().as_str(), "1" | "true" | "yes" | "on"))
        .unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    #[allow(unsafe_code)] // test-only env mutation (DGP_FLAG_TEST)
    fn truthy_values_are_recognized() {
        let _l: std::sync::Mutex<()> = std::sync::Mutex::new(());
        for truthy in ["1", "true", "TRUE", "yes", "Yes", "on"] {
            unsafe { std::env::set_var("DGP_FLAG_TEST", truthy) };
            assert!(env_flag("DGP_FLAG_TEST"), "{truthy}");
        }
        for falsy in ["", "0", "off", "no", "false"] {
            unsafe { std::env::set_var("DGP_FLAG_TEST", falsy) };
            assert!(!env_flag("DGP_FLAG_TEST"), "{falsy:?}");
        }
        unsafe { std::env::remove_var("DGP_FLAG_TEST") };
        assert!(!env_flag("DGP_FLAG_TEST"));
    }
}
