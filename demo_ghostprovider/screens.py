"""Cyberpunk-themed screens for demo_ghostprovider."""

import asyncio
import logging
import random
import re
import shutil
import time

from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, Center
from rich.text import Text
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import (
    Button, Input, Static, ProgressBar,
    RichLog, ListView, ListItem, Switch,
)

from demo_ghostprovider.analyzer import run_analysis, AnalysisResult, fingerprint_port
from demo_ghostprovider.hoster import (
    analyze_repo, host_project, cleanup, preflight_check,
    verify_deployment, RepoAnalysis,
)
from demo_ghostprovider.services import (
    list_services, start_service, stop_service, restart_service,
    remove_service,
    wait_service_ready, service_urls,
)


def _hex() -> str:
    return f"0x{random.randint(0x1000, 0xFFFF):04x}"


logger = logging.getLogger("demo_ghostprovider")


def _safe_task(coro) -> asyncio.Task:
    """Create a background task and attach an error handler so exceptions
    are not silently swallowed."""
    task = asyncio.create_task(coro)

    def _done_cb(t: asyncio.Task) -> None:
        try:
            t.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Background task failed")

    task.add_done_callback(_done_cb)
    return task

class MatrixRain(Widget):
    """Full-screen Matrix-style digital rain animation."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._messages: list[tuple[str, str, bool]] = []
        self._typing = ""
        self._progress = (0, 0)
        self._status = ""

    # ── Public API (matches BootSequence) ────────────────────────────

    def on_mount(self) -> None:
        pass

    def set_progress(self, current: int, total: int) -> None:
        self._progress = (current, total)
        self.refresh()

    def set_status(self, text: str) -> None:
        self._status = text
        self.refresh()

    def reset(self) -> None:
        self._messages.clear()
        self._typing = ""
        self._progress = (0, 0)
        self._status = ""

    def write_ok(self, label: str, addr: str = "") -> None:
        self._messages.append((label, addr, True))
        self._typing = ""
        self.refresh()

    def write_fail(self, label: str, detail: str = "") -> None:
        self._messages.append((label, detail, False))
        self._typing = ""
        self.refresh()

    def write_msg(self, label: str) -> None:
        self._messages.append((label, "", None))
        self._typing = ""
        self.refresh()

    async def typewrite_ok(self, label: str, addr: str = "", speed: float = 0.02) -> None:
        text = f"{addr}  {label}" if addr else label
        self._typing = ""
        for ch in text:
            self._typing += ch
            self.refresh()
            await asyncio.sleep(speed)
        self._messages.append((label, addr, True))
        self._typing = ""
        self.refresh()

    async def typewrite_msg(self, label: str, speed: float = 0.02) -> None:
        self._typing = ""
        for ch in label:
            self._typing += ch
            self.refresh()
            await asyncio.sleep(speed)
        self._messages.append((label, "", None))
        self._typing = ""
        self.refresh()

    async def typewrite_status(self, text: str, speed: float = 0.03) -> None:
        self._typing = ""
        for ch in text:
            self._typing += ch
            self.refresh()
            await asyncio.sleep(speed)
        self._typing = text
        self.refresh()

    def get_visible_text(self) -> str:
        lines: list[str] = []
        for label, extra, ok in self._messages:
            status = "[ OK ]" if ok else "[FAIL]"
            if extra:
                lines.append(f"{status}  {extra}  {label}")
            else:
                lines.append(f"{status}  {label}")
        if self._typing:
            lines.append(f">>> {self._typing}")
        if self._status:
            lines.append(f"  {self._status}")
        return "\n".join(lines)

    # ── Render ──────────────────────────────────────────────────────

    def render(self) -> Text:
        w = self.size.width
        h = self.size.height
        if w <= 0 or h <= 0:
            return Text()

        rows = [Text(" " * w) for _ in range(h)]

        overlay: list[tuple[str, object]] = []
        if self._progress[1] > 0:
            overlay.append(("progress", self._progress))
        for msg in self._messages:
            overlay.append(("msg", msg))
        if self._typing:
            overlay.append(("typing", self._typing))

        if overlay:
            lines: list[Text] = []
            for kind, data in overlay:
                if kind == "progress":
                    cur, tot = data  # type: ignore[misc]
                    pct = f" {cur}/{tot} "
                    bar_w = min(40, w - 10)
                    filled = int(bar_w * cur / tot)
                    bar = "█" * filled + "░" * (bar_w - filled)
                    t = Text()
                    t.append(f"[{bar}]", Style(bold=True, color="#00ff00"))
                    t.append(pct, Style(bold=True, color="#00ff00"))
                    lines.append(t)
                elif kind == "msg":
                    label, extra, ok = data  # type: ignore[misc]
                    if ok is None:
                        color = "#00ff00"
                        text = label
                    else:
                        status = "[  OK  ]" if ok else "[FAILED]"
                        color = "#00ff00" if ok else "#ff0000"
                        text = f"{status}"
                        if extra:
                            text += f"  {extra}"
                        text += f"  {label}"
                    lines.append(Text(text, Style(bold=True, color=color)))
                elif kind == "typing":
                    text = data  # type: ignore[assignment]
                    lines.append(Text(f">>> {text}", Style(color="#00cc00")))

            max_w = max(t.cell_len for t in lines) if lines else 0
            pad = max(0, (w - max_w) // 2)
            mid = max(1, h // 2 - len(lines) // 2)
            for i, t in enumerate(lines):
                r = mid + i
                if 0 <= r < h:
                    rows[r] = Text(" " * pad) + t

        if self._status and h > 1:
            sr = h - 2
            if 0 <= sr < h:
                sep = "─" * (w - 4)
                t = Text(f"  {sep}", Style(color="#004400"))
                rows[sr] = t
            sr = h - 1
            if 0 <= sr < h:
                t = Text(f"  {self._status}  ", Style(bold=True, color="#00ff00"))
                rows[sr] = t

        result = Text()
        for i, row in enumerate(rows):
            if i > 0:
                result.append("\n")
            result.append(row)
        return result


# ── Main Menu Screen ───────────────────────────────────────────────

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


# ── Analysis Screen (Matrix rain) ─────────────────────────────────────

class AnalysisScreen(Screen):
    BINDINGS = [
        ("escape", "pop_screen"),
        ("left", "pop_screen"),
    ]

    def action_pop_screen(self) -> None:
        self.app.pop_screen()

    def compose(self) -> ComposeResult:
        yield MatrixRain(id="matrix-rain")

    def on_mount(self) -> None:
        rain = self.query_one(MatrixRain)
        _safe_task(self._run_scan(rain))

    async def _animate_dots(self, rain: MatrixRain, base: str, duration: float = 2.0, speed: float = 0.3) -> None:
        end = time.monotonic() + duration
        while time.monotonic() < end:
            for dots in [".", "..", "..."]:
                rain.set_status(f"{base}{dots}")
                await asyncio.sleep(speed)
                if time.monotonic() >= end:
                    break

    async def _run_scan(self, rain: MatrixRain) -> None:
        TOTAL = 8
        rain.set_progress(0, TOTAL)

        await rain.typewrite_status("initializing localhost connection...", speed=0.04)
        await asyncio.sleep(0.5)
        rain.set_progress(1, TOTAL)

        await rain.typewrite_status("authenticating kernel access...", speed=0.04)
        await asyncio.sleep(0.3)
        rain.set_progress(2, TOTAL)

        await rain.typewrite_status("scanning environment...", speed=0.04)
        await asyncio.sleep(0.3)
        rain.set_progress(3, TOTAL)

        rain.set_progress(4, TOTAL)
        await self._animate_dots(rain, "network analysis")
        rain.write_msg("network analysis")

        rain.set_progress(5, TOTAL)
        await self._animate_dots(rain, "port analysis")
        rain.write_msg("port analysis")

        rain.set_progress(6, TOTAL)
        rain.set_status("scanning localhost...")
        await asyncio.sleep(0.3)

        rain.set_progress(7, TOTAL)
        rain.set_status("analyzing system...")
        result = await self._run_analysis_thread()

        rain.set_progress(0, len(result.summary_items))
        for i, (label, ok) in enumerate(result.summary_items):
            if ok:
                await rain.typewrite_msg(label, speed=0.015)
            else:
                rain.write_msg(f"✗ {label}")
            rain.set_progress(i + 1, len(result.summary_items))
            await asyncio.sleep(0.1)

        if result.all_ok:
            rain.set_status("ALL GATEWAYS NOMINAL — Enter to proceed")
        else:
            rain.set_status("SYSTEM COMPROMISED — Enter to continue")

        self._result = result

    async def _run_analysis_thread(self) -> AnalysisResult:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, run_analysis)

    def on_key(self, event) -> None:
        if event.key == "enter" and hasattr(self, "_result"):
            self.app.push_screen(GithubScreen())


# ── GitHub Input Screen ────────────────────────────────────────────

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


# ── Work Directory Prompt ──────────────────────────────────────────

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
# ── Result Screen ───────────────────────────────────────────────────

class RepoResultScreen(Screen):
    BINDINGS = [
        ("escape", "pop_screen"),
        ("left", "pop_screen"),
    ]

    def action_pop_screen(self) -> None:
        self.app.pop_screen()

    def __init__(self, url: str, work_dir: str | None = None):
        self._url = url
        self._work_dir = work_dir
        self._deploy_result = None
        super().__init__()

    def compose(self) -> ComposeResult:
        yield MatrixRain(id="matrix-rain")

    def on_mount(self) -> None:
        rain = self.query_one(MatrixRain)
        _safe_task(self._animate_result(rain))

    async def _animate_result(self, rain: MatrixRain) -> None:
        await rain.typewrite_status("initializing target acquisition...", speed=0.04)
        await asyncio.sleep(0.3)

        # Pre-check: git must be installed before we can clone
        if not shutil.which("git"):
            rain.write_fail("Git is not installed", detail="ERR")
            rain.write_fail("install git and try again", detail="ERR")
            rain.set_status("Enter — return")
            return

        await rain.typewrite_status("cloning repository...", speed=0.04)
        await asyncio.sleep(0.2)

        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None, lambda: analyze_repo(self._url, work_dir=self._work_dir),
            )
        except Exception as e:
            rain.write_fail(f"ANALYSIS FAILED: {e}", detail="ERR")
            rain.write_fail("check network connection and URL", detail="ERR")
            rain.set_status("Enter — return")
            self._repo_result = None
            return

        await rain.typewrite_status("decompiling structure...", speed=0.04)
        await asyncio.sleep(0.2)

        rain.set_progress(0, 5)
        await rain.typewrite_ok(result.url, addr="TARGET", speed=0.02)
        await rain.typewrite_ok(result.owner or "?", addr="OWNER", speed=0.02)
        await rain.typewrite_ok(result.name or "?", addr="REPO", speed=0.02)
        rain.set_progress(1, 5)

        exists_str = "VERIFIED" if result.exists else "NOT FOUND"
        await rain.typewrite_ok(exists_str, addr="STATUS", speed=0.02)
        await rain.typewrite_ok(result.language, addr="LANG", speed=0.02)
        rain.set_progress(2, 5)

        if result.errors:
            for err in result.errors:
                rain.write_fail(err, detail="ERROR")
            await asyncio.sleep(0.2)

        found_files = []
        if result.has_package_json:
            found_files.append("NODE")
        if result.has_requirements:
            found_files.append("PYTHON")
        if result.has_go_mod:
            found_files.append("GO")
        if result.has_cargo:
            found_files.append("RUST")
        if result.has_index:
            found_files.append("HTML")
        if found_files:
            await rain.typewrite_ok(f"Files: {', '.join(found_files)}", addr="DEPS", speed=0.01)
        rain.set_progress(3, 5)

        # Show app category
        cat_labels = {
            "media_server": "MEDIA SERVER",
            "web_app": "WEB APP",
            "api_server": "API SERVER",
            "search_engine": "SEARCH ENGINE",
            "desktop_app": "DESKTOP APP",
            "cli": "CLI TOOL",
            "library": "LIBRARY",
            "unknown": "UNKNOWN",
        }
        cat_label = cat_labels.get(result.app_category, result.app_category.upper())
        await rain.typewrite_ok(cat_label, addr="TYPE", speed=0.02)

        if result.app_category == "search_engine":
            await rain.typewrite_ok(
                "🔍 Search engine — serves HTML, works in browser",
                addr="INFO", speed=0.01,
            )
        elif not result.web_app_verified:
            await rain.typewrite_ok(
                result.category_reason or "⚠ May not work in browser",
                addr="WARN", speed=0.01,
            )

        # Show deep analysis
        if result.web_framework:
            await rain.typewrite_ok(
                f"web: {result.web_framework}",
                addr="FRAME", speed=0.02,
            )
        if result.has_http_server:
            await rain.typewrite_ok("HTTP server detected in source", addr="SERVE", speed=0.02)
        if result.has_cli and not result.has_http_server:
            await rain.typewrite_ok("CLI tool (no HTTP server)", addr="CLI", speed=0.02)
        if result.has_desktop_gui:
            await rain.typewrite_ok("Desktop/GUI application", addr="GUI", speed=0.02)
        if result.is_library:
            await rain.typewrite_ok("Library-type project", addr="LIB", speed=0.02)

        rain.set_progress(4, 5)
        if result.can_host:
            score_str = f"SCORE {result.host_score}/100"
            label = "✓ TARGET COMPATIBLE" if result.host_score >= 50 else "⚠ LOW CONFIDENCE"
            await rain.typewrite_ok(f"{label}  {score_str}", addr="", speed=0.02)
            await rain.typewrite_ok(result.host_recommendation, addr="", speed=0.02)
            rain.set_progress(5, 5)
            rain.set_status(
                "══ ENTER — LAUNCH ══   (Esc — back)"
            )
        else:
            rain.write_fail("✗ TARGET INCOMPATIBLE", detail="")
            rain.write_fail(result.reason, detail="")
            rain.set_status("Enter — return")

        self._repo_result = result

    def _start_hosting(self, result: RepoAnalysis) -> None:
        wd = getattr(self, "_work_dir", None)
        self.app.push_screen(HostingScreen(result=result, work_dir=wd))

    def confirm_and_deploy(self, result: RepoAnalysis) -> None:
        self._deploy_result = result
        msg = (
            f"[bold red]Do you really want to host this service?[/bold red]\n\n"
            f"[yellow]Target:[/yellow] {result.url}\n"
            f"[yellow]Stack:[/yellow] {result.language}\n"
            f"[yellow]Verdict:[/yellow] {result.host_recommendation}"
        )
        if result.host_score < 50:
            msg += (
                "\n\n[red]Low hosting confidence — browser may show an empty page.\n"
                "Still launch it?[/red]"
            )
        self.app.push_screen(ConfirmModal(msg), self._on_deploy_confirmed)

    def _on_deploy_confirmed(self, confirmed: bool | None) -> None:
        if confirmed:
            result = self._deploy_result
            if result is not None:
                self._start_hosting(result)
            return
        result = self._deploy_result
        if result is not None:
            cleanup(result)
        main = self.app.get_screen("main")
        main.query_one("#btn-analyze", Button).focus()
        self.app.switch_screen("main")

    def on_key(self, event) -> None:
        if event.key == "enter" and hasattr(self, "_repo_result"):
            event.stop()
            result = self._repo_result
            if result.can_host:
                self.confirm_and_deploy(result)
            else:
                cleanup(result)
                main = self.app.get_screen("main")
                main.query_one("#btn-analyze", Button).focus()
                self.app.switch_screen("main")


# ── Hosting Screen ──────────────────────────────────────────────────

class HostingScreen(Screen):
    BINDINGS = [
        ("escape", "pop_screen"),
        ("left", "pop_screen"),
    ]

    DEFAULT_CSS = """
    HostingScreen {
        background: #000;
    }
    #hosting-container {
        background: #000;
    }
    RichLog {
        background: #000;
    }
    ProgressBar {
        background: #000;
    }
    """

    def action_pop_screen(self) -> None:
        self.app.pop_screen()

    def __init__(self, result: RepoAnalysis, work_dir: str | None = None):
        self._result = result
        self._work_dir = work_dir
        super().__init__()

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("[bold red]╔══ DEPLOY SEQUENCE ══╗[/bold red]", id="host-title"),
            RichLog(id="host-log", highlight=True, markup=True),
            Center(
                ProgressBar(total=4, id="host-progress", show_eta=False),
            ),
            id="hosting-container",
        )

    def on_mount(self) -> None:
        _safe_task(self._animate_hosting())

    async def _typewrite(self, widget: RichLog, text: str, speed: float = 0.015) -> None:
        widget.write(text)
        await asyncio.sleep(speed)

    async def _animate_hosting(self) -> None:
        log = self.query_one("#host-log", RichLog)
        prog = self.query_one("#host-progress", ProgressBar)

        await self._typewrite(log, f"  [yellow]{_hex()}[/yellow] [dim]initializing deployment...[/dim]")
        await asyncio.sleep(0.3)
        prog.update(progress=1)

        loop = asyncio.get_running_loop()

        try:
            # Pre-flight checks
            await self._typewrite(log, f"  [yellow]{_hex()}[/yellow] [dim]pre-flight checks...[/dim]")
            issues = await loop.run_in_executor(None, preflight_check)

            if issues:
                systemd_missing = any("systemd not found" in i for i in issues)
                other_issues = [i for i in issues if "systemd" not in i.lower()]

                if systemd_missing and not other_issues:
                    await self._typewrite(log, "  [yellow]  systemd not found[/yellow]")
                    await self._typewrite(log, "  [red]→ systemd is required for service management[/red]")
                    self._done = True
                    await self._typewrite(log, "  [dim yellow]Enter to return[/dim yellow]")
                    return
                else:
                    for iss in issues:
                        await self._typewrite(log, f"  [yellow]  ⚠ {iss}[/yellow]")
                    await self._typewrite(log, "  [red]→ pre-flight checks failed, aborting[/red]")
                    self._done = True
                    await self._typewrite(log, "  [dim yellow]Enter to return[/dim yellow]")
                    return

            await self._typewrite(log, f"  [yellow]{_hex()}[/yellow] [dim]deploying service...[/dim]")
            prog.update(progress=2)

            def _on_status(line: str) -> None:
                if line.strip():
                    self.app.call_from_thread(
                        lambda: log.write(Text(f"  {line[:120]}", style="dim"))
                    )

            host_result = await loop.run_in_executor(
                None, lambda: host_project(
                    self._result, 0,
                    verify=False, work_dir=self._work_dir,
                    on_status=_on_status,
                ),
            )

            # If no services were created (clone failed etc.), show errors and bail
            if not host_result.service_names:
                await self._typewrite(log, "")
                await self._typewrite(log, "  [bold red]  ✗ DEPLOYMENT FAILED[/bold red]")
                if host_result.errors:
                    for err in host_result.errors:
                        await self._typewrite(log, f"  [red]    {err[:200]}[/red]")
                else:
                    await self._typewrite(log, "  [red]  No services created[/red]")
                await self._typewrite(log, "  [dim yellow]Enter to return[/dim yellow]")
                self._done = True
                return

            prog.update(progress=3)

            await self._typewrite(log, f"  [yellow]{_hex()}[/yellow] [dim]verifying service...[/dim]")
            host_result = await loop.run_in_executor(
                None, lambda: verify_deployment(host_result, 60, on_status=_on_status),
            )
            prog.update(progress=4)

            await self._typewrite(log, "")
            # Labels for multi-endpoint services
            _url_labels = {
                1: "Admin panel",
            }
            if host_result.healthy:
                for i, url in enumerate(host_result.urls):
                    label = _url_labels.get(i, "Workspace" if i == 0 else "")
                    prefix = f"✓ DEPLOYED" if i == 0 else "  "
                    suffix = f" ({label})" if label else ""
                    await self._typewrite(log, f"  [bold green]  {prefix} AT {url}{suffix}[/bold green]")
                await self._typewrite(log, "  [dim green]target is live[/dim green]")
            elif host_result.urls:
                for i, url in enumerate(host_result.urls):
                    label = _url_labels.get(i, "Workspace" if i == 0 else "")
                    prefix = f"? RUNNING" if i == 0 else "  "
                    suffix = f" ({label})" if label else ""
                    await self._typewrite(log, f"  [bold yellow]  {prefix} AT {url}{suffix}[/bold yellow]")
                await self._typewrite(log, "  [dim yellow]service started but not yet responding — check back later.[/dim yellow]")
                if host_result.errors:
                    for err in host_result.errors:
                        await self._typewrite(log, f"  [red]    {err[:200]}[/red]")
            else:
                await self._typewrite(log, "  [bold red]  ✗ DEPLOYMENT FAILED[/bold red]")
                await self._typewrite(log, "  [red]  No accessible URLs found[/red]")
                if host_result.errors:
                    for err in host_result.errors:
                        await self._typewrite(log, f"  [red]    {err[:200]}[/red]")

            self._host_result = host_result

        except Exception as e:
            await self._typewrite(log, "")
            await self._typewrite(log, "  [bold red]  ✗ DEPLOYMENT FAILED[/bold red]")
            await self._typewrite(log, f"  [red]  {e}[/red]")

        await self._typewrite(log, "  [dim yellow]Enter to return[/dim yellow]")
        self._done = True

    def on_key(self, event) -> None:
        if event.key == "enter" and getattr(self, "_done", False):
            while not isinstance(self.app.screen, MainScreen):
                self.app.pop_screen()
            event.stop()


# ── Modals ──────────────────────────────────────────────────────────

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
            ),
        )
        yield Center(
            Static(
                "[dim red]←[/dim red] [dim]Yes  |  [/dim]"
                "[dim red]→[/dim red] [dim]No  |  [/dim]"
                "[dim red]Enter[/dim red] [dim]select  |  [/dim]"
                "[dim red]Esc[/dim red] [dim]cancel[/dim]",
                id="modal-hint",
            ),
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "modal-yes":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(False)
        elif event.key == "enter":
            focused = self.focused
            if focused and focused.id == "modal-yes":
                self.dismiss(True)
            elif focused and focused.id == "modal-no":
                self.dismiss(False)
        elif event.key == "right":
            self.query_one("#modal-no", Button).focus()
        elif event.key == "left":
            self.query_one("#modal-yes", Button).focus()


# ── Service Management Screen ──────────────────────────────────────

class ServiceListScreen(Screen):
    BINDINGS = [
        ("escape", "pop_screen"),
        ("left", "pop_screen"),
        ("enter", "toggle_selected"),
        ("e", "toggle_selected"),
        ("r", "restart_selected"),
        ("x", "remove_selected"),
        ("delete", "remove_selected"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._pending: dict[str, str] = {}
        self._removed_urls: dict[str, str] = {}
        self._show_all: bool = True
        self._refresh_lock = asyncio.Lock()
        self._fingerprints: dict[str, str] = {}
        self._fingerprint_cache_ports: set[int] = set()
        self._color_idx: int = 0
        self._blink_on: bool = True

    def action_pop_screen(self) -> None:
        self.app.pop_screen()

    def action_toggle_all(self) -> None:
        _safe_task(self._refresh(show_all=not self._show_all))

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("[bold #ffcc00]════ ACTIVE SERVICES ════[/bold #ffcc00]", id="services-title"),
            ListView(id="services-list"),
            Static(
                "[bold #ff0066]┌──────────────────────────────────────────────────────────────────────────────┐[/bold #ff0066]\n"
                "[bold #ff0066]│[/bold #ff0066]  [bold #00ffff]↑↓[/bold #00ffff] [dim]NAVIGATE[/dim]  [bold #cc00ff]Enter/E[/bold #cc00ff] [dim]TOGGLE[/dim]  [bold #ff6600]R[/bold #ff6600] [dim]RESTART[/dim]  [bold #ff3333]X/DEL[/bold #ff3333] [dim]REMOVE[/dim]  [bold #ff0066]← Esc[/bold #ff0066] [dim]BACK[/dim]  [bold #ff0066]│[/bold #ff0066]\n"
                "[bold #ff0066]└──────────────────────────────────────────────────────────────────────────────┘[/bold #ff0066]",
                id="services-hint",
            ),
            id="services-container",
        )

    def on_mount(self) -> None:
        _safe_task(self._refresh())
        self.set_interval(0.6, self._blink_animation)

    async def _refresh(self, show_all: bool | None = None) -> None:
        async with self._refresh_lock:
            if show_all is None:
                show_all = getattr(self, "_show_all", False)
            self._show_all = show_all
            self._containers = await asyncio.get_running_loop().run_in_executor(
                None, list_services, show_all
            )
            self._pending.clear()
            try:
                self._rebuild_rows()
            except Exception as e:
                if hasattr(self, "app") and self.app:
                    self.app.notify(f"Rebuild error: {e}", severity="error", timeout=5)

    def _blink_animation(self) -> None:
        self._blink_on = not self._blink_on
        if not hasattr(self, "_containers") or not self._containers:
            return
        try:
            list_view = self.query_one("#services-list", ListView)
        except Exception:
            return
        for i, child in enumerate(list_view.children):
            if i >= len(self._containers):
                break
            c = self._containers[i]
            if c.name in self._pending:
                continue
            try:
                row = child.query_one(".svc-row")
                ind = row.query_one(f"#svc-ind-{i}")
                if c.state == "running":
                    if self._blink_on:
                        ind.update("[bold #00ff00]◉[/bold #00ff00]")
                    else:
                        ind.update("[bold #003300]◉[/bold #003300]")
                elif c.state == "exited":
                    if self._blink_on:
                        ind.update("[bold #ff3333]◎[/bold #ff3333]")
                    else:
                        ind.update("[bold #330000]◎[/bold #330000]")
            except Exception:
                pass

    def _rebuild_rows(self) -> None:
        list_view = self.query_one("#services-list", ListView)
        list_view.clear()

        if not self._containers:
            list_view.append(
                ListItem(Static("[dim]  No services or systemd unavailable[/dim]"))
            )
            return

        for i, c in enumerate(self._containers):
            is_pending = c.name in self._pending
            state_text = self._pending.get(c.name, c.state)
            state_cls = "svc-status-pending" if is_pending else f"svc-status-{c.state}"
            if is_pending:
                switch_value = self._pending[c.name] == "starting"
            else:
                switch_value = c.state in ("active", "running")

            urls = service_urls(c)
            url_text = "  |  ".join(urls) if urls else "[dim]—[/dim]"
            port = int(urls[0].rsplit(":", 1)[-1]) if urls else 0

            svc_name = self._fingerprints.get(c.name) or self._fingerprints.get(str(port))
            if not svc_name and port and port not in self._fingerprint_cache_ports:
                self._fingerprint_cache_ports.add(port)
                try:
                    fp = fingerprint_port(port)
                    if fp and fp.confidence >= 75:
                        svc_name = fp.service_name
                        self._fingerprints[c.name] = svc_name
                        self._fingerprints[str(port)] = svc_name
                except Exception:
                    pass

            if svc_name:
                display_name = svc_name
            elif c.description:
                display_name = c.description.split(":")[-1].strip()[:30]
            else:
                display_name = c.name

            if is_pending:
                indicator = "[bold yellow]⟳[/bold yellow]"
            elif c.state in ("active", "running"):
                indicator = "[bold #00ff00]◉[/bold #00ff00]"
            elif c.state in ("failed", "inactive"):
                indicator = "[bold #ff3333]◎[/bold #ff3333]"
            else:
                indicator = "[dim]○[/dim]"

            ind_id = f"svc-ind-{i}"

            buttons = [Button("⊘", id=f"svc-rm-{c.name}", classes="svc-rm-btn")]

            item = ListItem(
                Horizontal(
                    Static(indicator, id=ind_id, classes="svc-ind"),
                    Static(display_name, classes="svc-name"),
                    Static(state_text, classes=f"svc-status {state_cls}"),
                    Static(url_text, classes="svc-url"),
                    Switch(value=switch_value, classes="svc-toggle"),
                    *buttons,
                    classes="svc-row",
                ),
            )
            list_view.append(item)

        if self._containers:
            list_view.index = 0

    def _toggle_at_index(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._containers):
            return
        service = self._containers[idx]
        action = "stop" if service.state in ("active", "running") else "start"
        label = "stopping" if action == "stop" else "starting"
        self._pending[service.name] = label
        self.set_timer(0, lambda: self._rebuild_rows())
        _safe_task(self._exec_action(action, service.name))

    def action_toggle_selected(self) -> None:
        list_view = self.query_one("#services-list", ListView)
        if list_view.index is None:
            return
        self._toggle_at_index(list_view.index)

    def action_restart_selected(self) -> None:
        list_view = self.query_one("#services-list", ListView)
        if list_view.index is None:
            return
        idx = list_view.index
        if 0 <= idx < len(self._containers):
            container = self._containers[idx]
            self._pending[container.name] = "restarting"
            self.set_timer(0, lambda: self._rebuild_rows())
            _safe_task(self._exec_restart(container.name))

    def action_remove_selected(self) -> None:
        list_view = self.query_one("#services-list", ListView)
        if list_view.index is None:
            return
        idx = list_view.index
        if 0 <= idx < len(self._containers):
            container = self._containers[idx]
            self._ask_remove_confirm(container.name)

    def on_switch_changed(self, event: Switch.Changed) -> None:
        list_view = self.query_one("#services-list", ListView)
        for idx, child in enumerate(list_view.children):
            try:
                if child.query_one(Switch) is event.switch:
                    if 0 <= idx < len(self._containers):
                        name = self._containers[idx].name
                        if name not in self._pending:
                            self._toggle_at_index(idx)
                    return
            except Exception:
                pass

    async def _exec_action(self, action: str, name: str) -> None:
        loop = asyncio.get_running_loop()
        try:
            if action == "start":
                msg = await loop.run_in_executor(None, start_service, name)
                await loop.run_in_executor(None, wait_service_ready, name)
            elif action == "stop":
                msg = await loop.run_in_executor(None, stop_service, name)
            if not msg.startswith("Failed") and not msg.startswith("Timeout") and "not available" not in msg:
                if hasattr(self, "app") and self.app:
                    self.app.notify(msg, timeout=3)
            else:
                if hasattr(self, "app") and self.app:
                    self.app.notify(msg, severity="error", timeout=5)
        except Exception as e:
            if hasattr(self, "app") and self.app:
                self.app.notify(f"Action error: {e}", severity="error", timeout=5)
        await self._refresh()

    async def _exec_restart(self, name: str) -> None:
        loop = asyncio.get_running_loop()
        try:
            msg = await loop.run_in_executor(None, restart_service, name)
            await loop.run_in_executor(None, wait_service_ready, name)
            if not msg.startswith("Failed") and "not available" not in msg:
                if hasattr(self, "app") and self.app:
                    self.app.notify(msg, timeout=3)
            else:
                if hasattr(self, "app") and self.app:
                    self.app.notify(msg, severity="error", timeout=5)
        except Exception as e:
            if hasattr(self, "app") and self.app:
                self.app.notify(f"Restart error: {e}", severity="error", timeout=5)
        await self._refresh()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id.startswith("svc-rm-"):
            name = btn_id[len("svc-rm-"):]
            self._ask_remove_confirm(name)

    def _ask_remove_confirm(self, name: str) -> None:
        """Show confirmation dialog before removing a service."""
        service = None
        for c in (getattr(self, "_containers", None) or []):
            if c.name == name:
                service = c
                break

        display_name = name
        if service:
            if service.description:
                display_name = service.description.split(":")[-1].strip()
            urls = service_urls(service)
            url_text = urls[0] if urls else "no port"

            msg = (
                f"[yellow]Service:[/yellow] {display_name}\n"
                f"[yellow]Status:[/yellow] {service.state}\n"
                f"[yellow]Address:[/yellow] {url_text}\n\n"
                f"[red]This will permanently remove:\n"
                f"  • systemd unit file\n"
                f"  • working directory and all files\n"
                f"  • process state\n\n"
                f"Are you sure?[/red]"
            )
        else:
            msg = f"[red]Delete service '{name}'?[/red]"

        self.app.push_screen(
            ConfirmModal(msg, title="DELETE"),
            lambda confirmed: self._on_remove_confirmed(confirmed, name),
        )

    def _on_remove_confirmed(self, confirmed: bool, name: str) -> None:
        if confirmed:
            _safe_task(self._exec_remove(name))

    async def _exec_remove(self, name: str) -> None:
        loop = asyncio.get_running_loop()
        try:
            for c in (getattr(self, "_containers", None) or []):
                if c.name == name:
                    # Try to get repo URL from service description
                    repo_url = ""
                    if hasattr(c, "exec_start") and "github.com" in c.exec_start:
                        m = re.search(r'https?://github\.com/\S+', c.exec_start)
                        if m:
                            repo_url = m.group(0)
                    if repo_url:
                        self._removed_urls[name] = repo_url
                    break
            msg = await loop.run_in_executor(None, remove_service, name)
            if "error" in msg.lower() or "failed" in msg.lower():
                if hasattr(self, "app") and self.app:
                    self.app.notify(msg, severity="error", timeout=5)
            else:
                if hasattr(self, "app") and self.app:
                    self.app.notify(msg, timeout=3)
        except Exception as e:
            if hasattr(self, "app") and self.app:
                self.app.notify(f"Remove error: {e}", severity="error", timeout=5)
        await self._refresh()
