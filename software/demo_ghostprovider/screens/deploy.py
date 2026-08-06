"""Repo result and hosting (deploy sequence) screens."""

import asyncio
import shutil

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Center, Vertical
from textual.screen import Screen
from textual.widgets import Button, ProgressBar, RichLog, Static

from demo_ghostprovider.hoster import (
    analyze_repo, host_project, cleanup, preflight_check,
    verify_deployment, RepoAnalysis,
)
from demo_ghostprovider.screens.main import MainScreen
from demo_ghostprovider.screens.modals import ConfirmModal
from demo_ghostprovider.screens.widgets import MatrixRain, _hex, _safe_task


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
