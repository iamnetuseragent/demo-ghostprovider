"""GitHub URL and work directory input screens."""

from textual.app import ComposeResult
from textual.containers import Center, Vertical
from textual.screen import Screen
from textual.widgets import Input, Static

from demo_ghostprovider.screens.deploy import RepoResultScreen


class GithubScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("[bold red]╔══ ENTER THE ABYSS ══╗[/bold red]", id="github-title"),
            Center(
                Static(
                    "[yellow]Paste a GitHub repository URL below.\n"
                    "Ghostprovider will analyse whether it can be hosted.[/yellow]",
                    id="github-desc",
                ),
            ),
            Input(
                placeholder="https://github.com/user/repository",
                id="github-input",
            ),
            Center(
                Static(
                    "[dim red]Enter[/dim red] [dim]analyse  |  [/dim]"
                    "[dim red]← Esc[/dim red] [dim]return[/dim]",
                    id="github-hint",
                ),
            ),
            id="github-container",
        )

    def on_mount(self) -> None:
        self.query_one("#github-input", Input).focus()

    def on_show(self) -> None:
        inp = self.query_one("#github-input", Input)
        inp.value = ""
        inp.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        url = event.value.strip()
        if url:
            self.app.push_screen(WorkDirPromptScreen(url=url))

    def on_key(self, event) -> None:
        if event.key in ("escape", "left"):
            self.app.pop_screen()


class WorkDirPromptScreen(Screen):
    BINDINGS = [
        ("escape", "pop_screen"),
        ("left", "pop_screen"),
    ]

    DEFAULT_CSS = """
    WorkDirPromptScreen {
        background: #000;
    }
    #wd-container {
        width: 100%;
        height: 100%;
        background: #000;
    }
    #wd-title {
        align: center top;
        padding: 1 0;
        text-align: center;
    }
    #wd-desc {
        align: center middle;
        text-align: center;
        padding: 0 2;
    }
    #wd-input {
        margin: 0 4;
    }
    #wd-hint {
        align: center middle;
        color: #660000;
        margin: 1 0;
    }
    """

    def __init__(self, url: str):
        self._url = url
        super().__init__()

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(
                "[bold red]╔══ WORK DIRECTORY ══╗[/bold red]",
                id="wd-title",
            ),
            Center(
                Static(
                    "[yellow]Which directory to clone the repository into?\n"
                    "Leave empty for a temporary folder.[/yellow]",
                    id="wd-desc",
                ),
            ),
            Input(
                placeholder="~/demo_ghostprovider (Enter — confirm, Esc — back)",
                id="wd-input",
            ),
            Center(
                Static(
                    "[dim red]Enter[/dim red] [dim]continue  |  [/dim]"
                    "[dim red]Esc[/dim red] [dim]back[/dim]",
                    id="wd-hint",
                ),
            ),
            id="wd-container",
        )

    def on_mount(self) -> None:
        self.call_after_refresh(self._focus_input)

    def _focus_input(self) -> None:
        self.query_one("#wd-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        val = event.value.strip()
        work_dir = val if val else None
        self.app.push_screen(RepoResultScreen(url=self._url, work_dir=work_dir))

    def on_key(self, event) -> None:
        if event.key in ("escape", "left"):
            self.app.pop_screen()
