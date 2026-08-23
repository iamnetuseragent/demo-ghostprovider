//! Analyzer data model.

/// A TCP port found listening on the machine.
#[derive(Debug, Clone)]
pub struct ListeningPort {
    pub port: u16,
    pub address: String,
    pub process: String,
}

/// Fingerprint of an HTTP service discovered on a port.
#[derive(Debug, Clone)]
pub struct ServiceFingerprint {
    pub port: u16,
    pub service_type: String,
    pub service_name: String,
    pub confidence: u8,
    pub server_header: String,
    pub status_line: String,
}

#[derive(Debug, Clone)]
pub struct InterfaceInfo {
    pub name: String,
    pub ip: String,
    pub netmask: String,
    pub status: String,
}

/// Full result of the local system analysis.
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
    pub services: Vec<ServiceFingerprint>,
    pub vpn_active: bool,
    pub vpn_interfaces: Vec<String>,
    pub gateway: String,
    pub dns: Vec<String>,
    pub errors: Vec<String>,
}
