"""System analysis screen (Matrix rain boot sequence)."""

import asyncio
import time

from textual.app import ComposeResult
from textual.screen import Screen

from demo_ghostprovider.analyzer import run_analysis, AnalysisResult
from demo_ghostprovider.screens.widgets import MatrixRain, _safe_task


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
            self.app.push_screen("github")
