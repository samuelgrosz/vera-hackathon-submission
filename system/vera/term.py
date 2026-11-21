"""Terminal basics for Vera: colour, single keypresses, clearing, sizes.

Two modes:
  key mode   a real terminal: arrow keys, single keypresses, redrawn screens
  line mode  no real terminal (piped input, Git Bash without winpty, VERA_LINE_MODE=1):
             numbered menus, you type a choice and press Enter
Standard library only.
"""
from __future__ import annotations

import os
import shutil
import sys

IS_WIN = os.name == "nt"


class Quit(Exception):
    """Raised anywhere to leave Vera cleanly."""


def _enable_windows_ansi() -> None:
    if not IS_WIN:
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if k32.GetConsoleMode(h, ctypes.byref(mode)):
            k32.SetConsoleMode(h, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:  # noqa: BLE001
        pass


for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
_enable_windows_ansi()

INTERACTIVE = (sys.stdin.isatty() and sys.stdout.isatty()
               and not os.environ.get("VERA_LINE_MODE"))
COLOR = (sys.stdout.isatty() or bool(os.environ.get("FORCE_COLOR"))) and not os.environ.get("NO_COLOR")

# Vera palette (256-colour ANSI). Violet is the brand colour.
STYLES = {
    "bold": "1", "dim": "2", "italic": "3", "under": "4", "inverse": "7",
    "violet": "38;5;141", "violet2": "38;5;135", "lilac": "38;5;183",
    "green": "38;5;114", "red": "38;5;210", "amber": "38;5;221",
    "blue": "38;5;111", "grey": "38;5;245", "white": "38;5;255",
    "bg_violet": "48;5;55",
}

LOGO_GRADIENT = ("38;5;189", "38;5;183", "38;5;177", "38;5;141", "38;5;135", "38;5;99")


def c(text: str, *styles: str) -> str:
    if not COLOR or not styles:
        return text
    codes = ";".join(STYLES.get(s, s) for s in styles)
    return f"\x1b[{codes}m{text}\x1b[0m"


def visible_len(s: str) -> int:
    """Length without ANSI colour codes, for padding coloured text."""
    import re
    return len(re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", s))


def pad(s: str, n: int) -> str:
    return s + " " * max(0, n - visible_len(s))


def width() -> int:
    return max(60, min(shutil.get_terminal_size((100, 30)).columns, 120))


def height() -> int:
    return max(16, shutil.get_terminal_size((100, 30)).lines)


def write(s: str) -> None:
    sys.stdout.write(s)
    sys.stdout.flush()


def clear() -> None:
    if INTERACTIVE:
        write("\x1b[2J\x1b[H")
    else:
        write("\n" + c("─" * width(), "grey") + "\n")


def hide_cursor() -> None:
    if INTERACTIVE:
        write("\x1b[?25l")


def show_cursor() -> None:
    if INTERACTIVE:
        write("\x1b[?25h")


# --- keys ------------------------------------------------------------------------

def _getkey_windows() -> str:
    import msvcrt
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):
        k = msvcrt.getwch()
        return {"H": "up", "P": "down", "K": "left", "M": "right", "G": "home",
                "O": "end", "I": "pgup", "Q": "pgdn"}.get(k, "")
    if ch == "\x03":
        raise KeyboardInterrupt
    return {"\r": "enter", "\n": "enter", "\x1b": "esc", "\x08": "backspace"}.get(ch, ch)


def _getkey_posix() -> str:
    import select
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = os.read(fd, 1).decode(errors="ignore")
        if ch == "\x1b":
            ready, _, _ = select.select([fd], [], [], 0.03)
            if not ready:
                return "esc"
            seq = os.read(fd, 6).decode(errors="ignore")
            return {"[A": "up", "[B": "down", "[C": "right", "[D": "left", "[H": "home",
                    "[F": "end", "[5~": "pgup", "[6~": "pgdn", "OA": "up", "OB": "down",
                    "OC": "right", "OD": "left"}.get(seq, "")
        if ch == "\x03":
            raise KeyboardInterrupt
        return {"\r": "enter", "\n": "enter", "\x7f": "backspace", "\x08": "backspace"}.get(ch, ch)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def getkey() -> str:
    """One keypress as a name: 'up', 'down', 'left', 'right', 'enter', 'esc',
    'backspace', 'home', 'end', 'pgup', 'pgdn', or the character itself."""
    return _getkey_windows() if IS_WIN else _getkey_posix()


def read_line(prompt: str) -> str:
    """Typed input (both modes). End of input leaves Vera."""
    show_cursor()
    try:
        return input(prompt)
    except EOFError:
        raise Quit from None
