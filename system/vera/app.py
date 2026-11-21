"""Vera: the analyst-facing command-line app.

    vera [data folder]

Screens (Esc / ← always goes back, q quits):
  Home ─┬─ Run Vera            decide every hit, show the summary (+ practice score)
        ├─ Review queue        open alerts → an alert's hits → one hit
        │                        hit: decision tree · draft emails · analyst decision
        │                             · full profile notes · web search results
        ├─ Find by ID          jump straight to an alert (A-…) or hit (H-…)
        ├─ Decision guide      interactive yes/no tree through the SOP
        ├─ Draft all emails    one internal (+ customer) draft per REVIEW hit
        └─ Help

Vera never changes the graded output (output/dev.jsonl, system/run). Its own files
live in .vera/ (last run, analyst decisions) and emails/ (drafts), both gitignored.
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from datetime import datetime
from typing import Optional

from steps.data import Data, as_list, load, search_query
from steps.model import DIFFERENT, REVIEW, SAME, Decision

from . import emails, engine_bridge, guide, splash, term, widgets
from .term import Quit, c

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORK = os.path.join(ROOT, ".vera")
EMAIL_DIR = os.path.join(ROOT, "emails")

# Cost matrix from the brief: COST[truth][said]
COST = {
    DIFFERENT: {DIFFERENT: 0, SAME: 3, REVIEW: 3},
    SAME: {DIFFERENT: 60, SAME: 0, REVIEW: 2},
    REVIEW: {DIFFERENT: 60, SAME: 2, REVIEW: 0},
}


class Session:
    def __init__(self, folder: str):
        self.folder = folder
        self.data: Optional[Data] = None
        self.decisions: dict[str, Decision] = {}
        self.hits_by_id: dict[str, dict] = {}
        self.overrides: dict[str, dict] = {}
        self.last_run: Optional[str] = None
        self.run_seconds = 0.0
        self._status = "starting"

    # --- loading and running ---------------------------------------------------------
    def load(self) -> None:
        def on_table(name):
            self._status = f"Loading {name.replace('_', ' ')}"
        self.data = splash.run_with_spinner(lambda: load(self.folder, on_table), lambda: self._status)
        self.hits_by_id = {h["hit_id"]: h for h in self.data.hits}
        self._load_overrides()

    def run(self) -> None:
        assert self.data
        progress = {"i": 0, "n": len(self.data.hits)}

        def on_hit(i, n):
            progress["i"], progress["n"] = i + 1, n

        t0 = time.time()
        # The graded engine decides; Vera's own loader (self.data) is only for display.
        decisions, rows, engine_data = splash.run_with_spinner(
            lambda: engine_bridge.decide_all(self.folder, on_hit),
            lambda: "Deciding hits  " + splash.progress_bar(progress["i"], progress["n"]))
        self.run_seconds = time.time() - t0
        self.decisions = {d.hit_id: d for d in decisions}
        os.makedirs(WORK, exist_ok=True)
        engine_bridge.write_last_run(rows, engine_data, os.path.join(WORK, "last_run.jsonl"))
        self.last_run = datetime.now().strftime("%H:%M")

    def ensure_run(self) -> None:
        if not self.decisions:
            term.clear()
            term.write("\n" + "\n".join(splash.logo_lines()) + "\n\n")
            self.run()

    # --- analyst decisions (never touch the graded output) -----------------------------
    def _overrides_path(self) -> str:
        return os.path.join(WORK, "overrides.jsonl")

    def _load_overrides(self) -> None:
        self.overrides = {}
        if os.path.exists(self._overrides_path()):
            with open(self._overrides_path(), encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        row = json.loads(line)
                        self.overrides[row["hit_id"]] = row

    def set_override(self, hit_id: str, decision: str, note: str) -> None:
        os.makedirs(WORK, exist_ok=True)
        row = {"hit_id": hit_id, "decision": decision, "note": note,
               "at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}
        with open(self._overrides_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.overrides[hit_id] = row

    # --- derived views ------------------------------------------------------------------
    def alert_hits(self, alert_id: str) -> list[Decision]:
        a = self.data.alerts_by_id.get(alert_id, {})
        ids = as_list(a.get("hit_ids")) or [h["hit_id"] for h in self.data.hits if h["alert_id"] == alert_id]
        return [self.decisions[i] for i in ids if i in self.decisions]

    def open_alerts(self) -> list[tuple[dict, list[Decision]]]:
        out = []
        for a in self.data.alerts:
            ds = self.alert_hits(a["alert_id"])
            if any(d.decision != DIFFERENT for d in ds):
                out.append((a, ds))
        # matches first, then most hits under review
        out.sort(key=lambda x: (-sum(d.decision == SAME for d in x[1]),
                                -sum(d.decision == REVIEW for d in x[1]), x[0]["alert_id"]))
        return out

    def status_line(self) -> str:
        folder = os.path.relpath(self.folder, ROOT) if self.folder.startswith(ROOT) else self.folder
        n = len(self.data.hits) if self.data else 0
        ran = (c(f"last run {self.last_run}", "green") if self.last_run else c("not run yet", "amber"))
        return f"  {c('data', 'grey')} {folder}   {c('hits', 'grey')} {n}   {ran}"


# --- screens ----------------------------------------------------------------------------

def home(s: Session) -> None:
    items = [
        f"{c('Run Vera', 'bold')}            {c('decide every hit and see the summary', 'grey')}",
        f"{c('Review queue', 'bold')}        {c('work through the alerts still open', 'grey')}",
        f"{c('Find by ID', 'bold')}          {c('jump to an alert (A-…) or a hit (H-…)', 'grey')}",
        f"{c('Decision guide', 'bold')}      {c('walk the identity SOP step by step', 'grey')}",
        f"{c('Draft all emails', 'bold')}    {c('info requests for every review hit', 'grey')}",
        f"{c('Help', 'bold')}                {c('keys and how Vera works', 'grey')}",
    ]
    pos = 0
    while True:
        head = ("\n" + "\n".join(splash.logo_lines()) + "\n\n" + s.status_line() + "\n\n")
        choice = widgets.select(head, items, start=pos)
        if choice is widgets.BACK:
            if widgets.confirm("Leave Vera?"):
                raise Quit
            continue
        if isinstance(choice, int):
            pos = choice
            SCREENS[choice](s)


def run_screen(s: Session) -> None:
    term.clear()
    term.write(widgets.header_bar("Run", s.folder) + "\n")
    s.run()
    summary_screen(s)


def summary_screen(s: Session) -> None:
    ds = list(s.decisions.values())
    counts = Counter(d.decision for d in ds)
    alerts = s.data.alerts
    closed = sum(1 for a in alerts if (h := s.alert_hits(a["alert_id"])) and all(d.decision == DIFFERENT for d in h))
    n = len(ds) or 1
    bar_w = 30
    lines = [widgets.header_bar("Run summary", f"{s.run_seconds:.1f}s"), ""]
    for dec in (DIFFERENT, SAME, REVIEW):
        k = counts.get(dec, 0)
        colour = widgets.DECISION_STYLE[dec][1]
        lines.append(f"  {widgets.tag(dec)} {c('█' * round(bar_w * k / n), colour)}{c('░' * (bar_w - round(bar_w * k / n)), 'grey')}  {k:>4}  {c(f'{100 * k / n:.0f}%', 'grey')}")
    lines += ["", widgets.kv([
        ("Hits decided", f"{len(ds)}"),
        ("Alerts closed", f"{closed} of {len(alerts)}  {c('(every hit cleared)', 'grey')}"),
        ("Output", os.path.relpath(os.path.join(WORK, 'last_run.jsonl'), ROOT)),
    ])]
    lines += ["", practice_score(s)]
    choice = widgets.select("\n".join(lines) + "\n\n", ["Open the review queue", "Back to home"])
    if choice == 0:
        queue_screen(s)


def practice_score(s: Session) -> str:
    labels = s.data.tables.get("practice_labels") or []
    if not labels:
        return c("  No practice labels in this folder.", "grey")
    cost = base = unsafe = right = 0
    for lab in labels:
        d = s.decisions.get(lab["hit_id"])
        if not d:
            continue
        truth = lab["decision"]
        cost += COST[truth][d.decision]
        base += COST[truth][REVIEW]
        unsafe += COST[truth][d.decision] == 60
        right += truth == d.decision
    score = max(0.0, 1 - cost / base) if base else 0.0
    return (f"  {c('Practice labels', 'bold')}  ({len(labels)} hits)\n" + widgets.kv([
        ("Decision score", c(f"{100 * score:.1f}", "violet", "bold") + c("  share of cost saved vs all-REVIEW", "grey")),
        ("Right decisions", f"{right} of {len(labels)}"),
        ("Unsafe clearances", c(str(unsafe), "red" if unsafe else "green", "bold")),
    ]))


def queue_screen(s: Session) -> None:
    s.ensure_run()
    pos = 0
    while True:
        rows = s.open_alerts()
        items = []
        for a, ds in rows:
            cnt = Counter(d.decision for d in ds)
            marks = " ".join(f"{widgets.tag(k)}{c('×' + str(v), 'grey')}" for k, v in sorted(cnt.items()) if k != DIFFERENT)
            done = sum(1 for d in ds if d.hit_id in s.overrides)
            prog = c(f"  ✓{done}/{len(ds)}", "green") if done else ""
            items.append(f"{c(a['alert_id'], 'grey')}  {term.pad((a.get('customer_name') or '?')[:28], 29)} {marks}{prog}")
        head = widgets.header_bar("Review queue", f"{len(rows)} open alerts") + "\n"
        choice = widgets.select(head, items, start=pos, empty="Every alert is closed.")
        if choice is widgets.BACK:
            return
        if isinstance(choice, int):
            pos = choice
            alert_screen(s, rows[choice][0]["alert_id"])


def alert_screen(s: Session, alert_id: str) -> None:
    a = s.data.alerts_by_id.get(alert_id)
    if not a:
        widgets.pause(widgets.header_bar("Not found") + f"\n  No alert {alert_id}.\n")
        return
    pos = 0
    while True:
        ds = s.alert_hits(alert_id)
        link = (f"account {a['account_ref']}" if a.get("account_ref") else
                f"login {a['login_handle']}" if a.get("login_handle") else
                f"email {a['email']}" if a.get("email") else c("no link: customer must be searched for", "amber"))
        head = (widgets.header_bar(f"Alert {alert_id}", f"screened {a.get('screened_at', '?')}") + "\n"
                + widgets.kv([
                    ("Screened name", c(a.get("customer_name") or "?", "bold")),
                    ("Type", a.get("entity_type") or "?"),
                    ("Date of birth", a.get("dob") or c("not given", "grey")),
                    ("Country / city", " / ".join(x for x in (a.get("country"), a.get("city")) if x) or c("not given", "grey")),
                    ("Link", link),
                ]) + "\n\n  " + c(f"{len(ds)} hit(s)", "bold") + "\n")
        items = []
        for d in ds:
            h = s.hits_by_id.get(d.hit_id, {})
            ov = s.overrides.get(d.hit_id)
            mark = (c("  analyst: ", "grey") + widgets.tag(ov["decision"])) if ov else ""
            cat = h.get("primary_category") or (as_list(h.get("categories")) or ["?"])[0]
            items.append(f"{widgets.tag(d.decision)} {c(d.hit_id, 'grey')}  {term.pad((h.get('name') or '?')[:30], 31)}"
                         f"{c(cat, 'blue')}  {c('score ' + str(h.get('match_score', '?')), 'grey')}{mark}")
        choice = widgets.select(head, items, start=pos)
        if choice is widgets.BACK:
            return
        if isinstance(choice, int):
            pos = choice
            hit_screen(s, ds[choice].hit_id)


def hit_screen(s: Session, hit_id: str) -> None:
    d = s.decisions.get(hit_id)
    h = s.hits_by_id.get(hit_id)
    if not d or not h:
        widgets.pause(widgets.header_bar("Not found") + f"\n  No hit {hit_id}.\n")
        return
    a = s.data.alerts_by_id.get(d.alert_id, {})
    actions = ["Show decision tree", "Draft emails", "Record analyst decision",
               "Read full profile notes", "Web search results for this name"]
    pos = 0
    while True:
        w = term.width()
        col = (w - 22) // 2
        def row(label, left, right):
            return f"  {c(label.ljust(18), 'grey')}{term.pad(str(left or '—')[:col - 2], col)}{str(right or '—')[:col]}"
        cat = h.get("primary_category") or (as_list(h.get("categories")) or ["?"])[0]
        countries = sorted({*as_list(h.get("countries")), *as_list(h.get("nationality")), *as_list(h.get("citizenship"))})
        lines = [widgets.header_bar(f"Hit {hit_id}", f"alert {d.alert_id}"), "",
                 f"  {' ' * 18}{c(term.pad('CUSTOMER (as screened)', col), 'violet', 'bold')}{c('LISTED PROFILE', 'violet', 'bold')}",
                 row("Name", a.get("customer_name"), h.get("name")),
                 row("Aliases", "", "; ".join(as_list(h.get("aliases"))[:4])),
                 row("Type", a.get("entity_type"), h.get("entity_type")),
                 row("Date of birth", a.get("dob"), h.get("dobs")),
                 row("Countries", " / ".join(x for x in (a.get("country"), a.get("city")) if x), ", ".join(countries)),
                 row("Category", "", cat),
                 row("Status", "", h.get("life_status") or ""),
                 "",
                 f"  {widgets.badge(d.decision)}  {c(d.reason_code, 'bold')}   {c('customer record', 'grey')} {d.customer_record_id or '—'}",
                 widgets.wrap(d.reason, indent=2),
                 f"  {c('evidence', 'grey')} {', '.join(sorted(set(d.evidence))) or '—'}"]
        ov = s.overrides.get(hit_id)
        if ov:
            lines.append(f"  {c('analyst decision', 'grey')} {widgets.tag(ov['decision'])} {c(ov.get('note') or '', 'italic')}  {c(ov['at'], 'grey')}")
        notes = (h.get("profile_notes") or "").strip()
        if notes:
            first = notes.split("\n")[0]
            lines += ["", widgets.wrap(c((first[:280] + ("…" if len(first) > 280 or "\n" in notes else "")), "grey"))]
        choice = widgets.select("\n".join(lines) + "\n\n", actions, start=pos,
                                actions={"t": "tree", "e": "emails", "d": "decide"})
        if choice is widgets.BACK:
            return
        choice = {"t": 0, "e": 1, "d": 2}.get(choice, choice)
        pos = choice
        if choice == 0:
            widgets.pause(widgets.header_bar("Decision tree", hit_id) + "\n" + guide.render_path(d))
        elif choice == 1:
            email_screen(s, d)
        elif choice == 2:
            override_screen(s, d)
        elif choice == 3:
            widgets.pause(widgets.header_bar("Profile notes", hit_id) + "\n" + widgets.wrap(notes or "No profile notes.") + "\n")
        elif choice == 4:
            search_screen(s, h)


def override_screen(s: Session, d: Decision) -> None:
    head = (widgets.header_bar("Analyst decision", d.hit_id) + "\n"
            + widgets.wrap("Record your own decision for this hit. It is saved in .vera/overrides.jsonl "
                           "for the team's review log and does not change Vera's graded output.") + "\n\n")
    opts = [(DIFFERENT, "Different person: clear the hit"),
            (SAME, "Same person: escalate to compliance"),
            (REVIEW, "Still unsure: keep it open")]
    choice = widgets.select(head, [f"{widgets.tag(k)}  {v}" for k, v in opts])
    if not isinstance(choice, int):
        return
    note = widgets.ask("Note (optional)")
    s.set_override(d.hit_id, opts[choice][0], note)


def email_screen(s: Session, d: Decision) -> None:
    drafts = emails.drafts_for(d, s.data)
    if not drafts:
        widgets.pause(widgets.header_bar("Emails", d.hit_id) + "\n" + widgets.wrap(
            f"Vera only drafts info requests for hits left for review. This hit is "
            f"{d.decision}, so there is nothing to ask for.") + "\n")
        return
    i = 0
    while True:
        dr = drafts[i]
        label = "Internal: to the review team" if dr.kind == "internal" else "Customer: neutral details request"
        body = "\n".join("  " + c("│ ", "violet") + line for line in dr.preview().splitlines())
        note = ("" if dr.kind == "internal" else
                "\n\n" + widgets.wrap(c("Checked for tipping off: this draft mentions no screening, lists or matches.", "green")))
        head = (widgets.header_bar("Email drafts", f"{i + 1} of {len(drafts)}") + "\n  "
                + c(label, "bold") + "\n\n" + body + note + "\n\n")
        items = ["Save drafts to emails/"] + (["Next draft"] if len(drafts) > 1 else [])
        choice = widgets.select(head, items)
        if choice is widgets.BACK:
            return
        if choice == 0:
            paths = emails.save(drafts, EMAIL_DIR)
            widgets.pause(widgets.header_bar("Saved") + "\n" + "\n".join(
                f"  {c('✓', 'green')} {os.path.relpath(p, ROOT)}" for p in paths)
                + "\n\n" + widgets.wrap(c("Drafts are never sent. Open the .eml files in any mail app.", "grey")) + "\n")
            return
        i = (i + 1) % len(drafts)


def bulk_emails_screen(s: Session) -> None:
    s.ensure_run()
    reviews = [d for d in s.decisions.values() if d.decision == REVIEW]
    term.clear()
    term.write(widgets.header_bar("Draft all emails") + "\n")
    box = {"n": 0}

    def work():
        allp = []
        for k, d in enumerate(reviews):
            box["n"] = k + 1
            allp += emails.save(emails.drafts_for(d, s.data), EMAIL_DIR)
        return allp

    paths = splash.run_with_spinner(work, lambda: "Drafting  " + splash.progress_bar(box["n"], len(reviews)))
    kinds = Counter("customer" if p.endswith("_customer.eml") else "internal" for p in paths)
    widgets.pause(widgets.header_bar("Draft all emails", "done") + "\n" + widgets.kv([
        ("Review hits", str(len(reviews))),
        ("Internal drafts", str(kinds.get("internal", 0))),
        ("Customer drafts", str(kinds.get("customer", 0)) + c("  (only where the customer can help and has an email)", "grey")),
        ("Folder", os.path.relpath(EMAIL_DIR, ROOT) + c("  (gitignored, never sent)", "grey")),
    ]) + "\n")


def search_screen(s: Session, h: dict) -> None:
    q = search_query(h.get("name") or "")
    results = s.data.search_by_query.get(q, [])
    lines = [widgets.header_bar("Web search results", f"“{q}”"), "",
             widgets.wrap(c("Most results are about other people with the same name. A result is about "
                            "this hit only if it describes the same kind of listing in a country tied to it.", "grey")), ""]
    for r in results[:12]:
        lines.append(f"  {c(r.get('result_id', ''), 'violet')}  {c(r.get('title') or '', 'bold')}  {c(r.get('source') or '', 'grey')}")
        lines.append(widgets.wrap(r.get("snippet") or "", indent=4))
        lines.append("")
    if not results:
        lines.append("  " + c("No search results for this name.", "grey"))
    elif len(results) > 12:
        lines.append(c(f"  … and {len(results) - 12} more", "grey"))
    widgets.pause("\n".join(lines) + "\n")


def find_screen(s: Session) -> None:
    s.ensure_run()
    term.clear()
    term.write(widgets.header_bar("Find by ID") + "\n")
    q = widgets.ask("Alert or hit ID (e.g. A-ED1B2D or H-00E181)").strip().upper()
    if not q:
        return
    if q in s.data.alerts_by_id:
        alert_screen(s, q)
    elif q in s.decisions:
        hit_screen(s, q)
    else:
        widgets.pause(widgets.header_bar("Find by ID") + f"\n  Nothing called {c(q, 'bold')}.\n")


def help_screen(s: Session) -> None:
    keys = [("↑ ↓  or  j k", "move"), ("enter  or  →", "open"), ("1–9", "pick a numbered item"),
            ("esc  ←  backspace  b", "back"), ("t / e / d", "on a hit: tree, emails, decide"), ("q", "quit")]
    text = (widgets.header_bar("Help") + "\n  " + c("Keys", "bold") + "\n"
            + "\n".join(f"    {c(k.ljust(24), 'violet')}{v}" for k, v in keys) + "\n\n  " + c("How Vera decides", "bold") + "\n"
            + widgets.wrap("For each hit Vera first works out who the customer really is from the bank's systems, "
                           "then who the listed person really is, then compares them. It clears a hit only on a "
                           "contradiction (type, death, age, date of birth, verified name, location), confirms only "
                           "on agreement, and leaves everything else for a person.", indent=4) + "\n\n  "
            + c("Files", "bold") + "\n" + widgets.kv([
                (".vera/last_run.jsonl", "Vera's latest decisions"),
                (".vera/overrides.jsonl", "analyst decisions you record"),
                ("emails/", "email drafts (never sent)"),
            ], key_width=24) + "\n\n" + widgets.wrap(c(
                "Line mode: if your terminal can't read single keys (e.g. Git Bash), Vera shows numbered "
                "menus. Type a number and press enter.", "grey"), indent=4) + "\n")
    widgets.pause(text)


SCREENS = [run_screen, queue_screen, find_screen, lambda s: guide.run_guide(),
           bulk_emails_screen, help_screen]  # same order as the home menu


# --- entry ----------------------------------------------------------------------------

def default_folder() -> str:
    for name in ("dev", "datasets"):
        p = os.path.join(ROOT, name)
        if os.path.isdir(p):
            return p
    return os.getcwd()


def main(argv: Optional[list[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    folder = os.path.abspath(argv[0]) if argv else default_folder()
    if not os.path.isdir(folder):
        print(f"vera: no such data folder: {folder}", file=sys.stderr)
        return 2
    s = Session(folder)
    try:
        splash.intro()
        s.load()
        home(s)
    except (Quit, KeyboardInterrupt):
        pass
    finally:
        term.show_cursor()
        term.write("\n  " + splash.small_logo() + c("  see you next time.", "grey") + "\n\n")
    return 0
