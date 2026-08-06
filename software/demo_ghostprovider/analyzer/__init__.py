"""System & environment analysis for demo_ghostprovider."""

from demo_ghostprovider.analyzer.models import (
    AnalysisResult,
    InterfaceInfo,
    ListeningPort,
    NetworkInfo,
    ServiceFingerprint,
)
from demo_ghostprovider.analyzer.probe import (
    _check_cmd,
    _check_localhost,
    _check_network,
    _check_systemd_nspawn,
    _detect_interfaces,
    _detect_listening_ports,
    _detect_vpn,
    _fingerprint_all_services,
    _get_dns,
    _get_gateway,
    fingerprint_port,
    run_analysis,
)
from demo_ghostprovider.analyzer.signatures import SERVICE_SIGNATURES

__all__ = [
    "InterfaceInfo",
    "ListeningPort",
    "ServiceFingerprint",
    "NetworkInfo",
    "AnalysisResult",
    "SERVICE_SIGNATURES",
    "fingerprint_port",
    "run_analysis",
]
