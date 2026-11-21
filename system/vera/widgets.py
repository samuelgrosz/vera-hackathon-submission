"""Menus and small display pieces shared by every Vera screen.

select() is the one interaction primitive: show a header, a list of choices and
some hotkeys, and return what the user picked.
  key mode:  ↑/↓ (or j/k) move, Enter or → picks, number keys pick directly,
             Esc / ← / Backspace / b goes back, q quits
  line mode: type a number, a hotkey letter, b for back or q to quit
"""
from __future__ import annotations

import textwrap
from typing import Optional, Union

from . import term
from .term import Quit, c

BACK = None  # select() returns this when the user goes back

DECISION_STYLE = {
    "DIFFERENT_ENTITY": ("CLEARED", "green"),
    "SAME_ENTITY": ("MATCH", "red"),
    "REVIEW": ("REVIEW", "amber"),
}


def badge(decision: str) -> str:
    label, colour = DECISION_STYLE.get(decision, (decision, "grey"))
    return c(f" {label} ", colour, "inverse", "bold")


def tag(decision: str) -> str:
    """Short coloured word, for lists."""
    label, colour = DECISION_STYLE.get(decision, (decision, "grey"))
    return c(f"{label:<7}", colour, "bold")


def header_bar(title: str, crumbs: str = "") -> str:
    w = term.width()
    left = c(" ▼ VERA ", "white", "bg_violet", "bold") + "  " + c(title, "bold")
    right = c(crumbs, "grey")
    gap = max(1, w - term.visible_len(left) - term.visible_len(right))
    return left + " " * gap + right + "\n" + c("─" * w, "violet2") + "\n"


def wrap(text: str, indent: int = 2, width: Optional[int] = None) -> str:
    w = (width or term.width()) - indent
    pre = " " * indent
    out = []
    for para in str(text).splitlines() or [""]:
        out.append(textwrap.fill(para, w, initial_indent=pre, subsequent_indent=pre) if para else "")
    return "\n".join(out)


def kv(rows: list[tuple[str, str]], key_width: int = 18) -> str:
    return "\n".join(f"  {c(k.ljust(key_width), 'grey')}{v}" for k, v in rows)


def footer(hints: list[tuple[str, str]]) -> str:
    parts = [c(k, "violet", "bold") + " " + c(v, "grey") for k, v in hints]
    return "\n" + c("─" * term.width(), "grey") + "\n  " + "   ".join(parts) + "\n"


Choice = Union[int, str, None]


def select(header: str, items: list[str], actions: Optional[dict[str, str]] = None,
           start: int = 0, empty: str = "Nothing here.") -> Choice:
    """Show a menu. Returns the picked index (int), a hotkey (str), or BACK (None)."""
    actions = actions or {}
    return (_select_keys if term.INTERACTIVE else _select_lines)(header, items, actions, start, empty)


def _select_keys(header, items, actions, start, empty) -> Choice:
    pos = min(max(start, 0), max(len(items) - 1, 0))
    top = 0
    term.hide_cursor()
    while True:
        rows = max(4, term.height() - header.count("\n") - 6)
        if pos < top:
            top = pos
        elif pos >= top + rows:
            top = pos - rows + 1
        out = [header]
        if not items:
            out.append("  " + c(empty, "grey") + "\n")
        for i in range(top, min(len(items), top + rows)):
            num = c(f"{i + 1:>2}", "grey") if len(items) > 1 else "  "
            if i == pos:
                out.append(f" {c('›', 'violet', 'bold')}{num} {c(term.pad(items[i], 2), 'bold')}\n")
            else:
                out.append(f"  {num} {items[i]}\n")
        if len(items) > rows:
            out.append(c(f"     {top + 1}–{min(len(items), top + rows)} of {len(items)}", "grey") + "\n")
        hints = [("↑↓", "move"), ("enter", "open"), ("esc", "back")]
        hints += [(k, v) for k, v in actions.items()]
        hints.append(("q", "quit"))
        out.append(footer(hints))
        term.clear()
        term.write("".join(out))

        k = term.getkey()
        if k in ("up", "k") and items:
            pos = (pos - 1) % len(items)
        elif k in ("down", "j") and items:
            pos = (pos + 1) % len(items)
        elif k == "pgup":
            pos = max(0, pos - rows)
        elif k == "pgdn":
            pos = min(len(items) - 1, pos + rows)
        elif k == "home":
            pos = 0
        elif k == "end":
            pos = max(0, len(items) - 1)
        elif k in ("enter", "right") and items:
            return pos
        elif k in ("esc", "left", "backspace", "b"):
            return BACK
        elif k == "q":
            raise Quit
        elif k in actions:
            return k
        elif k.isdigit() and k != "0" and int(k) <= len(items) and len(items) <= 9:
            return int(k) - 1


def _select_lines(header, items, actions, start, empty) -> Choice:
    while True:
        out = [header]
        if not items:
            out.append("  " + c(empty, "grey") + "\n")
        for i, it in enumerate(items):
            out.append(f"  {c(str(i + 1).rjust(3), 'violet')}  {it}\n")
        legend = "   ".join(f"{c(k, 'violet')} {v}" for k, v in actions.items())
        out.append("\n  " + (legend + "   " if legend else "") + f"{c('b', 'violet')} back   {c('q', 'violet')} quit\n")
        term.clear()
        term.write("".join(out))
        ans = term.read_line(c("  vera › ", "violet", "bold")).strip().lower()
        if ans in ("q", "quit", "exit"):
            raise Quit
        if ans in ("b", "back"):
            return BACK
        if ans in actions:
            return ans
        if ans.isdigit() and 1 <= int(ans) <= len(items):
            return int(ans) - 1
        term.write(c(f"  '{ans}' isn't an option here.\n", "red"))


def pause(screen: str, hint: str = "any key to go back") -> None:
    """Show a full screen of text and wait."""
    term.clear()
    term.write(screen + footer([("any key" if term.INTERACTIVE else "enter", hint.replace('any key ', ''))]))
    if term.INTERACTIVE:
        term.hide_cursor()
        if term.getkey() == "q":
            raise Quit
    else:
        if term.read_line(c("  vera › ", "violet", "bold")).strip().lower() == "q":
            raise Quit


def ask(prompt: str, default: str = "") -> str:
    shown = f" [{default}]" if default else ""
    ans = term.read_line(f"  {c(prompt, 'bold')}{c(shown, 'grey')}: ").strip()
    return ans or default


def confirm(prompt: str) -> bool:
    return ask(prompt + " (y/n)", "n").lower().startswith("y")
