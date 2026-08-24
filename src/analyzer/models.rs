//! Analyzer data model.

/// A TCP port found listening on the machine.
#[derive(Debug, Clone)]
pub struct ListeningPort {
    pub port: u16,
    pub address: String,
    pub process: String,
}

#[derive(Debug, Clone)]
pub struct InterfaceInfo {
    pub name: String,
    pub ip: String,
    pub netmask: String,
    pub status: String,
}

/// Full result of the local system analysis.
///
/// Deliberately privacy-minimal: prerequisite tool checks plus a bare map of
/// occupied ports and their owning processes. No VPN detection and no HTTP
/// fingerprinting of local services — this report must stay useless to
/// anyone but the operator.
#[derive(Debug, Default)]
pub struct AnalysisResult {
    pub systemd: bool,
    pub systemd_nspawn: bool,
    pub git: bool,
    pub python3: bool,
    pub node: bool,
    pub localhost: bool,
    pub network: bool,
    pub interfaces: Vec<InterfaceInfo>,
    pub listening_ports: Vec<ListeningPort>,
    pub gateway: String,
    pub dns: Vec<String>,
    pub errors: Vec<String>,
}
