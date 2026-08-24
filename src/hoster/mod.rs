//! Deployment engine: curated recipes → hardened systemd user services.

pub mod deploy;
pub mod gitclone;
pub mod github;
pub mod httpclient;
pub mod models;
pub mod port;
pub mod preflight;
pub mod recipes;
pub mod sandbox;
pub mod secrets;
pub mod toolcheck;
pub mod units;
pub mod validate;
