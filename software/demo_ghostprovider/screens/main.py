"""Main menu screen."""

from textual.app import ComposeResult
from textual.containers import Center, Vertical
from textual.screen import Screen
from textual.widgets import Button, Static

from demo_ghostprovider.screens.analysis import AnalysisScreen
from demo_ghostprovider.screens.services import ServiceListScreen


class MainScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(
                "[bold yellow]⎈ DEMO PROJECT ⎈[/bold yellow]\n\n"
                "[red]Your data is your life.\n"
                "Fail to protect it, and you fail to protect your future.\n"
                "Only you decide what that future will be.[/red]",
                id="description",
            ),
            Center(
                Button("▶  INITIALIZE SYSTEM SCAN  ◀", id="btn-analyze", variant="primary"),
            ),
            Center(
                Button("☰  MANAGE ACTIVE SERVICES  ☰", id="btn-services", variant="default"),
            ),
            Static(
                "[dim red]────────────────────────────────[/dim red]\n"
                "[dim red]↑↓[/dim red] [dim]navigate  |  [/dim]"
                "[dim red]Enter[/dim red] [dim]select  |  [/dim]"
                "[dim red]← Esc[/dim red] [dim]exit  |  [/dim]"
                "[dim red]Ctrl+Shift+C[/dim red] [dim]copy[/dim]",
                id="hint",
            ),
            id="main-container",
        )

    def on_mount(self) -> None:
        self.query_one("#btn-analyze", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-analyze":
            self.app.push_screen(AnalysisScreen())
        elif event.button.id == "btn-services":
            self.app.push_screen(ServiceListScreen())

    def on_key(self, event) -> None:
        if event.key in ("escape", "left"):
            self.app.exit()
        elif event.key == "enter":
            focused = self.focused
            if focused and focused.id == "btn-analyze":
                self.app.push_screen(AnalysisScreen())
            elif focused and focused.id == "btn-services":
                self.app.push_screen(ServiceListScreen())
        elif event.key == "down":
            btns = self.query(Button)
            for i, b in enumerate(btns):
                if b is self.focused:
                    nxt = btns[i + 1] if i + 1 < len(btns) else btns[0]
                    nxt.focus()
                    return
            btns.first().focus()
        elif event.key == "up":
            btns = self.query(Button)
            for i, b in enumerate(btns):
                if b is self.focused:
                    nxt = btns[i - 1] if i - 1 >= 0 else btns[-1]
                    nxt.focus()
                    return
            btns.last().focus()
