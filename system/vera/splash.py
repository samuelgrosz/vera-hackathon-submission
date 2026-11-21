"""Vera's logo, the opening animation and the loading spinner."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

from . import term
from .term import c

# The V mark. Each row is drawn in one step of the violet gradient.
V_MARK = (
    "██╗   ██╗",
    "██║   ██║",
    "╚██╗ ██╔╝",
    " ╚████╔╝ ",
    "  ╚██╔╝  ",
    "   ╚═╝   ",
)

WORDMARK = "V  E  R  A"
TAGLINE = "same person or not?  ·  watchlist identity review"
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _gradient(row: str, i: int) -> str:
    if not term.COLOR:
        return row
    code = term.LOGO_GRADIENT[min(i, len(term.LOGO_GRADIENT) - 1)]
    return f"\x1b[{code};1m{row}\x1b[0m"


def logo_lines(indent: int = 4) -> list[str]:
    pad = " " * indent
    lines = [pad + _gradient(r, i) for i, r in enumerate(V_MARK)]
    lines[2] += "    " + c(WORDMARK, "white", "bold")
    lines[3] += "    " + c(TAGLINE, "grey")
    return lines


def small_logo() -> str:
    return c("▼", "violet", "bold") + " " + c("VERA", "bold")


def intro() -> None:
    """Draw the V row by row, then the wordmark letter by letter."""
    term.clear()
    term.hide_cursor()
    if not term.INTERACTIVE:
        term.write("\n" + "\n".join(logo_lines()) + "\n\n")
        return
    pad = " " * 4
    term.write("\n")
    for i, row in enumerate(V_MARK):
        term.write(pad + _gradient(row, i) + "\n")
        time.sleep(0.06)
    # wordmark and tagline typed in beside rows 3 and 4
    term.write(f"\x1b[4A\x1b[{4 + len(V_MARK[0]) + 4 + 1}G")
    for ch in WORDMARK:
        term.write(c(ch, "white", "bold"))
        time.sleep(0.03)
    term.write(f"\x1b[1B\x1b[{4 + len(V_MARK[0]) + 4 + 1}G" + c(TAGLINE, "grey"))
    term.write("\x1b[3B\r\n")
    time.sleep(0.15)


def run_with_spinner(fn: Callable[[], Any], status: Callable[[], str]) -> Any:
    """Run fn() in a thread and animate a spinner with status() until it finishes.
    Exceptions from fn are re-raised here."""
    box: dict[str, Any] = {}

    def work():
        try:
            box["result"] = fn()
        except BaseException as e:  # noqa: BLE001
            box["error"] = e

    t = threading.Thread(target=work, daemon=True)
    t.start()
    i = 0
    last = ""
    t0 = time.time()
    while t.is_alive():
        msg = status()
        if term.INTERACTIVE:
            frame = c(SPINNER[i % len(SPINNER)], "violet", "bold")
            line = f"    {frame}  {msg}  {c(f'{time.time() - t0:4.1f}s', 'grey')}"
            term.write("\r\x1b[2K" + line)
        elif msg != last:
            term.write(f"    … {msg}\n")
            last = msg
        i += 1
        t.join(0.08)
    if term.INTERACTIVE:
        term.write("\r\x1b[2K")
    if "error" in box:
        raise box["error"]
    return box.get("result")


def progress_bar(done: int, total: int, width: int = 24) -> str:
    frac = 0 if not total else done / total
    full = int(frac * width)
    return c("█" * full, "violet") + c("░" * (width - full), "grey") + c(f" {done}/{total}", "grey")
