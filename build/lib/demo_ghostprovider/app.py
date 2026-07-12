"""Demo GhostProvider — cyberpunk 2077 themed TUI application (limited to 3 services)."""

from textual.app import App, Binding

from demo_ghostprovider.screens import (
    MainScreen, AnalysisScreen, GithubScreen,
    ServiceListScreen,
    MatrixRain,
)

CYBERPUNK_CSS = """
Screen {
    background: #000;
}

Vertical {
    align: center middle;
    background: #000;
}

Center {
    align: center middle;
    width: 100%;
    background: #000;
}

Horizontal {
    background: #000;
}

Static {
    color: #cc0000;
    background: #000;
}

Button {
    background: #000;
    color: #ffcc00;
    border: tall #cc0000;
    padding: 1 4;
    min-width: 26;
    text-align: center;
}

Button:hover {
    background: #cc0000;
    color: #000000;
    border: tall #ffcc00;
}

Button:focus {
    background: #cc0000;
    color: #000000;
    border: tall #ffcc00;
}

Button.-primary {
    background: #cc0000;
    color: #ffcc00;
    border: tall #ffcc00;
}

Button.-primary:hover {
    background: #ffcc00;
    color: #000000;
    border: tall #cc0000;
}

Button.-primary:focus {
    background: #ffcc00;
    color: #000000;
    border: tall #cc0000;
}

Input {
    background: #000;
    color: #ff3333;
    border: tall #cc0000;
    padding: 0 2;
}

Input:focus {
    border: tall #ffcc00;
}

RichLog {
    background: #000;
    color: #cc0000;
    border: none;
    padding: 1;
    margin: 0 2;
}

ProgressBar {
    background: #000;
    color: #cc0000;
    border: none;
}

ProgressBar > .bar {
    background: #1a0000;
}

ProgressBar > .bar > .complete {
    background: #cc0000;
}

ProgressBar > .bar > .remaining {
    background: #1a0000;
}

Header {
    background: #000;
    color: #ffcc00;
}

Footer {
    background: #000;
    color: #660000;
}

Scrollbar {
    background: #1a0000;
}

Scrollbar > .thumb {
    background: #cc0000;
}

ListView {
    background: #000;
    border: none;
}

ListView > ListItem {
    background: #000;
    padding: 0 1;
    height: 3;
}

ListView > ListItem.--highlight {
    background: #2a0000;
}

ListView > ListItem > Horizontal {
    width: 100%;
}

#description {
    align: center middle;
    text-align: center;
    padding: 0 2;
}

#hint {
    align: center middle;
    color: #660000;
    margin: 1 0;
}

#btn-analyze {
    align: center middle;
    margin: 1 0;
}

BootSequence {
    width: 100%;
    height: 100%;
}

#github-title,
#result-title,
#host-title,
#services-title {
    align: center top;
    padding: 1 0;
    text-align: center;
}

#result-log,
#host-log {
    height: 14;
}

#host-progress {
    margin: 0 4;
}

#github-desc {
    align: center middle;
    text-align: center;
    padding: 0 2;
}

#github-input {
    margin: 0 4;
}

#github-hint {
    align: center middle;
    color: #660000;
    margin: 1 0;
}

#result-actions {
    align: center middle;
    margin: 1 0;
}

#modal-msg {
    align: center middle;
    text-align: center;
    padding: 2;
    background: #000;
    border: tall #cc0000;
    margin: 2 4;
}

#modal-buttons {
    align: center middle;
    margin: 1 0;
}

#services-hint {
    align: center middle;
    color: #ff0066;
    margin: 0 0 1 0;
    height: 3;
}

#services-title {
    content-align: center middle;
    text-align: center;
    height: 3;
    color: #ffcc00;
    text-style: bold;
}

#services-list {
    margin: 0 1;
    height: 1fr;
    background: #000;
}

#services-list > ListItem {
    height: 3;
    padding: 0 0;
}

#services-list > ListItem.--highlight {
    background: #1a0020;
    border: tall #ff0066;
}

/* ── Row ── */
.svc-row {
    width: 100%;
    height: 100%;
    align: left middle;
}

/* ── Indicator ── */
.svc-ind {
    width: 3;
    min-width: 3;
}

/* ── Name ── */
.svc-name {
    width: 30;
    min-width: 20;
    color: #00ffff;
    text-style: bold;
    padding: 0 1;
}

#services-list > ListItem.--highlight .svc-name {
    color: #ffffff;
}

#services-list > ListItem.--highlight .svc-status {
    color: #ffffff;
}

#services-list > ListItem.--highlight .svc-url {
    color: #ffffff;
}

/* ── Status ── */
.svc-status {
    width: 14;
    text-align: center;
    text-style: bold;
}

.svc-status-running    { color: #00ff00; }
.svc-status-exited     { color: #ff3333; }
.svc-status-paused     { color: #ffcc00; }
.svc-status-dead       { color: #ff0000; }
.svc-status-created    { color: #666666; }
.svc-status-restarting { color: #ffcc00; }
.svc-status-removing   { color: #ff0066; }
.svc-status-pending    { color: #ff6600; }

/* ── Address / URL ── */
.svc-url {
    width: 1fr;
    min-width: 24;
    color: #cc00ff;
    text-align: left;
    overflow: hidden;
    text-overflow: ellipsis;
    padding: 0 1;
}

/* ── Toggle ── */
Switch.svc-toggle {
    width: auto;
    min-width: 6;
    margin: 0 2;
    background: #330000;
    border: tall #ff3333;
}

Switch.svc-toggle.-on {
    background: #003300;
    border: tall #00ff00;
}

Switch.svc-toggle .switch--slider {
    color: #ffffff;
    background: #ff3333;
}

Switch.svc-toggle.-on .switch--slider {
    color: #ffffff;
    background: #00ff00;
}

/* ── Remove button ── */
Button.svc-rm-btn {
    color: #ff3333;
    background: #1a0000;
    border: tall #ff0066;
    padding: 0 1;
    min-width: 4;
}

Button.svc-rm-btn:hover {
    color: #000000;
    background: #ff0066;
}

#main-container,
#github-container,
#result-container,
#hosting-container,
#services-container {
    width: 100%;
    height: 100%;
    background: #000;
}

#services-list {
    background: #000;
}

#main-container Button {
    width: 50;
}
"""


class DemoGhostProviderApp(App):
    CSS = CYBERPUNK_CSS
    TITLE = "demo-ghostprovider"
    SUB_TITLE = "⎈ DEMO ⎈"

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
    app = DemoGhostProviderApp()
    app.run()
