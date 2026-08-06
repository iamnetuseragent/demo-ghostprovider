"""Shared widgets and helpers for demo_ghostprovider screens."""

import asyncio
import logging
import random

from rich.style import Style
from rich.text import Text
from textual.widget import Widget

logger = logging.getLogger("demo_ghostprovider")


def _hex() -> str:
    return f"0x{random.randint(0x1000, 0xFFFF):04x}"


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
