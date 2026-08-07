"""Screens for the demo_ghostprovider TUI."""

from demo_ghostprovider.screens.analysis import AnalysisScreen
from demo_ghostprovider.screens.deploy import HostingScreen, RepoResultScreen
from demo_ghostprovider.screens.github import GithubScreen, WorkDirPromptScreen
from demo_ghostprovider.screens.main import MainScreen
from demo_ghostprovider.screens.modals import ConfirmModal
from demo_ghostprovider.screens.services import ServiceListScreen
from demo_ghostprovider.screens.widgets import MatrixRain

__all__ = [
    "AnalysisScreen",
    "ConfirmModal",
    "GithubScreen",
    "HostingScreen",
    "MainScreen",
    "MatrixRain",
    "RepoResultScreen",
    "ServiceListScreen",
    "WorkDirPromptScreen",
]
