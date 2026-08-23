"""Service management screen."""

import asyncio
import logging
import re
from typing import ClassVar

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, ListItem, ListView, Static, Switch

from demo_ghostprovider.analyzer import fingerprint_port
from demo_ghostprovider.screens.modals import ConfirmModal
from demo_ghostprovider.screens.widgets import _safe_task
from demo_ghostprovider.services import (
    list_services,
    remove_service,
    restart_service,
    service_urls,
    start_service,
    stop_service,
    wait_service_ready,
)

logger = logging.getLogger("demo_ghostprovider.screens.services")


class ServiceListScreen(Screen):
    BINDINGS: ClassVar[list[tuple[str, str]]] = [
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
        self._refresh_lock = asyncio.Lock()
        self._fingerprints: dict[str, str] = {}
        self._fingerprint_cache_ports: set[int] = set()
        self._color_idx: int = 0
        self._blink_on: bool = True

    def action_pop_screen(self) -> None:
        self.app.pop_screen()

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("[bold #ffcc00]════ ACTIVE SERVICES ════[/bold #ffcc00]", id="services-title"),
            ListView(id="services-list"),
            Static(
                "[bold #ff0066]┌──────────────────────────────────────────────────────────────────────────────┐[/bold #ff0066]\n"
                "[bold #ff0066]│[/bold #ff0066]  [bold #00ffff]↑↓[/bold #00ffff] [dim]NAVIGATE[/dim]  [bold #cc00ff]Enter/E[/bold #cc00ff] [dim]TOGGLE[/dim]  [bold #ff6600]R[/bold #ff6600] [dim]RESTART[/dim]  [bold #ff3333]X/DEL[/bold #ff3333] [dim]REMOVE[/dim]  [bold #ff0066]← Esc[/bold #ff0066] [dim]BACK[/dim]  [bold #ff0066]│[/bold #ff0066]\n"
                "[bold #ff0066]└──────────────────────────────────────────────────────────────────────────────┘[/bold #ff0066]",
                id="services-hint",
            )
            ,
            id="services-container",
        )

    def on_mount(self) -> None:
        _safe_task(self._refresh())
        self.set_interval(0.6, self._blink_animation)

    async def _refresh(self) -> None:
        async with self._refresh_lock:
            self._containers = await asyncio.get_running_loop().run_in_executor(
                None, list_services
            )
            self._pending.clear()
            try:
                self._rebuild_rows()
            except Exception:
                logger.debug("Failed to rebuild service rows", exc_info=True)

    def _blink_animation(self) -> None:
        self._blink_on = not self._blink_on
        if not hasattr(self, "_containers") or not self._containers:
            return
        try:
            list_view = self.query_one("#services-list", ListView)
        except Exception:  # noqa: BLE001
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
            except Exception:  # noqa: BLE001, S110
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
                except Exception:  # noqa: BLE001, S110
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
                )
                ,
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
        self.call_after_refresh(self._rebuild_rows)
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
            self.call_after_refresh(self._rebuild_rows)
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
            except Exception:  # noqa: BLE001, S110
                pass

    async def _exec_action(self, action: str, name: str) -> None:
        loop = asyncio.get_running_loop()
        try:
            if action == "start":
                await loop.run_in_executor(None, start_service, name)
                await loop.run_in_executor(None, wait_service_ready, name)
            elif action == "stop":
                await loop.run_in_executor(None, stop_service, name)
        except Exception:
            logger.debug("Service action failed: %s %s", action, name, exc_info=True)
        await self._refresh()

    async def _exec_restart(self, name: str) -> None:
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, restart_service, name)
            await loop.run_in_executor(None, wait_service_ready, name)
        except Exception:
            logger.debug("Service restart failed: %s", name, exc_info=True)
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
                    repo_url = ""
                    if hasattr(c, "exec_start") and "github.com" in c.exec_start:
                        m = re.search(r'https?://github\.com/\S+', c.exec_start)
                        if m:
                            repo_url = m.group(0)
                    if repo_url:
                        self._removed_urls[name] = repo_url
                    break
            await loop.run_in_executor(None, remove_service, name)
        except Exception:
            logger.debug("Service remove failed: %s", name, exc_info=True)
        await self._refresh()
