"""GhostProvider — cyberpunk 2077 themed TUI application."""

from pathlib import Path
from textual.app import App, Binding

from demo_ghostprovider.screens import (
    MainScreen, AnalysisScreen, GithubScreen,
    ServiceListScreen,
    MatrixRain,
)


class GhostProviderApp(App):
    CSS_PATH = str(Path(__file__).parent / "theme.tcss")
    TITLE = "demo_ghostprovider"
    SUB_TITLE = "⎈"

    SCREENS = {
        "main": MainScreen,
        "github": GithubScreen,
    }

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("c", "copy_visible", "Copy all text", show=False),
        Binding("ctrl+shift+c", "copy_visible", "Copy selected text", show=False),
    ]

    def action_copy_visible(self) -> None:
        screen = self.screen
        rain = screen.query(MatrixRain).first()
        if rain is not None:
            text = rain.get_visible_text()
            if text:
                self.copy_to_clipboard(text)
                self._copy_via_wl(text)
            return
        screen.action_copy_text()

    def _copy_via_wl(self, text: str) -> None:
        import subprocess
        try:
            proc = subprocess.Popen(
                ["wl-copy"], stdin=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            proc.communicate(text.encode("utf-8"), timeout=1)
        except FileNotFoundError:
            pass
        except Exception:
            pass

    def on_mount(self) -> None:
        self.push_screen("main")


if __name__ == "__main__":
    app = GhostProviderApp()
    app.run()
