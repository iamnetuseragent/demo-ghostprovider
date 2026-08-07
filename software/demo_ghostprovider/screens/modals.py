"""Modal dialogs shared across screens."""

from textual.app import ComposeResult
from textual.containers import Center, Horizontal
from textual.screen import Screen
from textual.widgets import Button, Static


class ConfirmModal(Screen):
    DEFAULT_CSS = """
    ConfirmModal {
        background: #000;
    }
    #modal-title {
        align: center top;
        padding: 1 0;
        text-align: center;
    }
    #modal-hint {
        align: center middle;
        color: #660000;
        margin: 1 0;
    }
    """

    def __init__(self, message: str, title: str = "CONFIRM", yes_action: str = ""):
        self._message = message
        self._title = title
        self._yes_action = yes_action
        super().__init__()

    def on_mount(self) -> None:
        self.query_one("#modal-yes", Button).focus()

    def compose(self) -> ComposeResult:
        yield Static(f"[bold red]╔══ {self._title} ══╗[/bold red]", id="modal-title")
        yield Center(
            Static(self._message, id="modal-msg"),
        )
        yield Center(
            Horizontal(
                Button("  YES  ", id="modal-yes", variant="primary"),
                Button("  NO   ", id="modal-no", variant="default"),
                id="modal-buttons",
            )
            ,
        )
        yield Center(
            Static(
                "[dim red]←[/dim red] [dim]Yes  |  [/dim]"
                "[dim red]→[/dim red] [dim]No  |  [/dim]"
                "[dim red]Enter[/dim red] [dim]select  |  [/dim]"
                "[dim red]Esc[/dim red] [dim]cancel[/dim]",
                id="modal-hint",
            )
            ,
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "modal-yes":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(False)
        elif event.key == "enter":
            event.stop()
            focused = self.focused
            if focused and focused.id == "modal-yes":
                self.dismiss(True)
            elif focused and focused.id == "modal-no":
                self.dismiss(False)
        elif event.key == "right":
            event.stop()
            self.query_one("#modal-no", Button).focus()
        elif event.key == "left":
            event.stop()
            self.query_one("#modal-yes", Button).focus()
