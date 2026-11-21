"""Step 2 - customer resolution.

    ix = build_index(tables)                     # tables: {'alerts': [dict], 'customers': [...], ...}
    link, pics, note = resolve_with_index(alert_row, ix)   # one CustomerPicture per distinct person

Written by the Step 2 owner (originally in step2/), wired into system/ unchanged in
logic. Stdlib only. Name/date/country helpers come from `customer_compat`, a thin
bridge to the Step 1 matchers in system/match/.

Pipeline contract (main.py, Vera):
    resolve(alert, data) -> list[CustomerPicture]   one picture per distinct person
The index is built once per Data object and cached on it.
SOP principles implemented here: 2 (find the customer), 3 (one person, one picture),
4 (verified identity), and the notes part of 6 (places the notes say they lived).
"""
import re
from collections import defaultdict

import steps.customer_compat as C
from steps.model import CustomerPicture, Fact, PartialDate, Verified

USE_ACCOUNT_PHONE = False        # also treat accounts.phone as the owner's phone for merging
_STOP = {"DOB", "ID", "UK", "US", "USA", "UAE"}


def _s(v):
    """Cell -> clean string or None (handles NaN / blanks)."""
    if v is None:
        return None
    v = str(v).strip()
    return None if v == "" or v.lower() in ("nan", "none", "null") else v


def _pd(t):
    """(y, m, d) tuple from customer_compat -> model.PartialDate (None stays None)."""
    return PartialDate(*t) if t else None


def _digits(p):
    p = re.sub(r"\D", "", p or "")
    return p or None


# ----------------------------------------------------------------------------
# KYC-channel notes: "Persona confirmed the identity of ANNA MARTIN (DOB 16/01/1959) ..."
# ----------------------------------------------------------------------------
def parse_kyc_note(text):
    """-> {'name', 'dob' (partial date), 'doc_country' (ISO2)} (None where not found)."""
    dates = C.find_dates(text)
    masked, last = [], 0
    for s, e, _, _ in dates:
        masked.append(text[last:s])
        masked.append(" <D> ")
        last = e
    masked.append(text[last:])
    masked = "".join(masked)

    # name = longest run of ALL-CAPS tokens (placeholders such as N/A or 0000 can sit inside it)
    runs, cur = [], []
    for tok in masked.split():
        core = re.sub(r"['’]s$", "", tok.strip(",.;:()\"'"))
        namey = bool(core) and core == core.upper() and re.search(r"[A-ZÀ-ÿ0-9]", core) \
            and core not in _STOP and "<D>" not in tok
        if namey:
            cur.append(core)
            if tok.rstrip()[-1] in ",.;:)":
                runs.append(cur)
                cur = []
        else:
            if cur:
                runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    runs = [r for r in runs if any(re.search(r"[A-ZÀ-ÿ]", t) for t in r)]
    name = " ".join(max(runs, key=len)) if runs else None

    # date of birth = the date with a birth cue (before or right after it) or in parentheses
    dob = None
    for s, e, d, raw in dates:
        before = text[max(0, s - 45):s].lower()
        after = text[e:e + 25].lower()
        if re.search(r"(born|dob|date of birth|birth date|birth)[^0-9]{0,28}$", before) \
                or before.rstrip().endswith("(") \
                or re.match(r"\s*(?:(?:as|is|was) )?(?:the |his |her |their )?(date of birth|birth date|dob)", after):
            dob = d
            break
    if dob is None:                                # year-only birth date, e.g. "born 1966"
        for m in re.finditer(r"(?:born|dob|date of birth|birth date)[^0-9]{0,28}((?:19|20)\d{2})\b", text, re.I):
            if not any(s <= m.start(1) < e for s, e, _, _ in dates):
                dob = (int(m.group(1)), None, None)
                break
    if dob is None and dates:
        cands = [d for s, e, d, raw in dates
                 if not re.search(r"(\bon|\bby|issued|verification|\()[^0-9]{0,12}$", text[max(0, s - 22):s].lower())]
        dob = (cands or [dates[0][2]])[0]

    rest = masked.replace(name or "", " ")
    cs = C.countries_in_text(rest, exclude_names=[name] if name else ())
    return {"name": name, "dob": dob, "doc_countries": tuple(sorted(cs))}


# ----------------------------------------------------------------------------
class _UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)      # root = smallest id, deterministic


class Index:
    pass


def build_index(tables):
    ix = Index()
    ix.cust = {r["record_id"]: r for r in tables["customers"]}
    ix.acct_rows = defaultdict(list)             # account_id -> rows (joint accounts have 2)
    ix.acct_by_login = defaultdict(list)
    ix.accts_by_cust = defaultdict(list)
    for r in tables["accounts"]:
        ix.acct_rows[r["account_id"]].append(r)
        if _s(r.get("login_handle")):
            ix.acct_by_login[r["login_handle"]].append(r)
        ix.accts_by_cust[r["customer_id"]].append(r)
    ix.apps_by_cust = defaultdict(set)           # customer -> kyc applicant ids
    for r in tables["onboarding"]:
        if _s(r.get("kyc_applicant_id")):
            ix.apps_by_cust[r["customer_id"]].add(r["kyc_applicant_id"])
    ix.checks_by_app = defaultdict(list)
    for r in tables["kyc"]:
        ix.checks_by_app[r["applicant_id"]].append(r)
    ix.notes_by_email = defaultdict(list)
    for r in tables["notes"]:
        ix.notes_by_email[(r.get("requester_email") or "").lower()].append(r)
    ix.by_email = {(r.get("email") or "").lower(): r["record_id"] for r in tables["customers"] if _s(r.get("email"))}

    # per-record derived fields
    ix.dob, ix.parts, ix.cc = {}, {}, {}
    for rid, r in ix.cust.items():
        comp = r.get("entity_type") == "Company"
        ix.dob[rid] = C.parse_partial(r.get("dob"))
        ix.parts[rid] = [p for p in (C.name_parts(r.get(k), comp)
                                     for k in ("full_name", "preferred_name", "login_name")) if p]
        ix.cc[rid] = C.canon_country(r.get("residence_country")) | C.canon_country(r.get("nationality"))

    # blocking indexes for the no-link search
    ix.by_year, ix.by_country = defaultdict(list), defaultdict(list)
    for rid in ix.cust:
        d = ix.dob[rid]
        if d:
            ix.by_year[d[0]].append(rid)
        for c in ix.cc[rid]:
            ix.by_country[c].append(rid)

    # ---- principle 3: one person, one picture (union-find over ALL records) ----
    uf = _UF()
    by_phone_dob, by_doc = defaultdict(set), defaultdict(set)
    for rid, r in ix.cust.items():
        uf.find(rid)
        d, ph = ix.dob[rid], _digits(r.get("phone"))
        if ph and C.is_full(d):
            by_phone_dob[(ph, d)].add(rid)
        if USE_ACCOUNT_PHONE:
            for a in ix.accts_by_cust.get(rid, []):
                ph2 = _digits(a.get("phone"))
                if ph2 and C.is_full(d):
                    by_phone_dob[(ph2, d)].add(rid)
        for app in ix.apps_by_cust.get(rid, ()):
            for ck in ix.checks_by_app.get(app, ()):
                if (ck.get("result") or "").lower() == "approved" and _s(ck.get("document_number")):
                    by_doc[ck["document_number"].strip().upper()].add(rid)
    for group in list(by_phone_dob.values()) + list(by_doc.values()):
        g = sorted(group)
        for other in g[1:]:
            uf.union(g[0], other)
    ix.uf = uf
    members = defaultdict(list)
    for rid in ix.cust:
        members[uf.find(rid)].append(rid)
    ix.members = {k: sorted(v) for k, v in members.items()}
    ix.tables = tables
    return ix


# ----------------------------------------------------------------------------
# Principle 2: does a CRM record fit what was screened?
# ----------------------------------------------------------------------------
def fits(alert, rid, ix):
    r = ix.cust[rid]
    a_type, r_type = _s(alert.get("entity_type")), _s(r.get("entity_type"))
    if a_type and r_type and a_type != r_type:
        return False
    comp = (a_type == "Company")
    ap = C.name_parts(alert.get("customer_name"), comp)
    if not ap or not any(C.names_agree_parts(ap, rp) for rp in ix.parts[rid]):
        return False
    if not C.partial_equal(C.parse_partial(alert.get("dob")), ix.dob[rid]):
        return False
    country = C.canon_country(alert.get("country"))
    if country and not (country & ix.cc[rid]):
        return False
    city = _s(alert.get("city"))
    if city and not C.canon_country(city):       # a 'city' that is really a country is not a city test
        if not C.city_in(city, r.get("city"), r.get("address")):
            return False
    elif city and C.canon_country(city) and not country and not (C.canon_country(city) & ix.cc[rid]):
        return False
    return True


def _candidates_by_search(alert, ix):
    d = C.parse_partial(alert.get("dob"))
    country = C.canon_country(alert.get("country"))
    if d:
        pool = ix.by_year.get(d[0], [])
    elif country:
        pool = sorted({rid for c in country for rid in ix.by_country.get(c, [])})
    else:
        pool = list(ix.cust)
    return [rid for rid in pool if fits(alert, rid, ix)]


def _linked_holders(alert, ix):
    """-> (link_kind, [record ids the link points at])"""
    acc, login, email = _s(alert.get("account_ref")), _s(alert.get("login_handle")), _s(alert.get("email"))
    if acc:
        return "account", [r["customer_id"] for r in ix.acct_rows.get(acc, [])]
    if login:
        return "login", [r["customer_id"] for r in ix.acct_by_login.get(login, [])]
    if email:
        rid = ix.by_email.get(email.lower())
        return "email", [rid] if rid else []
    return "none", []


# ----------------------------------------------------------------------------
# Principles 3/4: build the picture of one person
# ----------------------------------------------------------------------------
def build_picture(root, ix):
    rids = ix.members[root]
    recs = [ix.cust[r] for r in rids]
    pic = CustomerPicture(primary_record_id=rids[0], record_ids=rids,
                          entity_type=_s(recs[0].get("entity_type")) or "Individual")
    evidence = set(rids)
    cands = []                                    # approved identity evidence (checks and notes)
    for rid in rids:
        r = ix.cust[rid]
        for kind, key in (("full_name", "full_name"), ("preferred_name", "preferred_name"),
                          ("login_name", "login_name")):
            if _s(r.get(key)):
                pic.names.append(Fact(kind=kind, value=r[key], source=rid))
        if ix.dob[rid]:
            pic.dobs.append(Fact(kind="crm_dob", value=_pd(ix.dob[rid]), source=rid, raw=_s(r.get("dob"))))
        for kind, key in (("residence", "residence_country"), ("nationality", "nationality")):
            for c in sorted(C.canon_country(r.get(key))):
                pic.countries.append(Fact(kind=kind, value=c, source=rid, raw=_s(r.get(key))))
        for kind, key in (("city", "city"), ("address", "address")):
            if _s(r.get(key)):
                pic.cities.append(Fact(kind=kind, value=r[key], source=rid))
        for app in sorted(ix.apps_by_cust.get(rid, ())):
            for ck in ix.checks_by_app.get(app, ()):
                if (ck.get("result") or "").lower() == "approved":
                    cands.append((C.parse_partial(ck.get("checked_at")) or (0, 0, 0), 0, Verified(
                        source=ck["check_id"], name=_s(ck.get("verified_name")),
                        dob=_pd(C.parse_partial(ck.get("verified_dob"))),
                        doc_countries=tuple(sorted(C.canon_country(ck.get("document_country")))),
                        when=_s(ck.get("checked_at")),
                        doc_number=_s(ck.get("document_number")), dob_raw=_s(ck.get("verified_dob")))))
        for n in ix.notes_by_email.get((r.get("email") or "").lower(), []):
            ch = (n.get("channel") or "").lower()
            if ch == "kyc":
                p = parse_kyc_note(n.get("text") or "")
                if p["name"] or p["dob"]:
                    cands.append((C.parse_partial(n.get("created_at")) or (0, 0, 0), 1, Verified(
                        source=n["note_id"], name=p["name"], dob=_pd(p["dob"]),
                        doc_countries=p["doc_countries"], when=_s(n.get("created_at")))))
            else:
                for c in sorted(C.countries_in_text(n.get("text") or "")):
                    pic.countries.append(Fact(kind="lived", value=c, source=n["note_id"], when=_s(n.get("created_at")), origin="note"))
                    evidence.add(n["note_id"])
    if cands:
        cands.sort(key=lambda t: (t[0], -t[1]))   # most recent; table check wins a same-day tie
        pic.verified = cands[-1][2]
        v = pic.verified
        evidence.add(v.source)
        if v.dob:
            pic.dobs.append(Fact(kind="verified_dob", value=v.dob, source=v.source, when=v.when, raw=v.dob_raw))
        for c in v.doc_countries:
            pic.countries.append(Fact(kind="document", value=c, source=v.source, when=v.when))
        if v.name:
            pic.legal_name = Fact(kind="verified_name", value=v.name, source=v.source, when=v.when)
            pic.names.append(pic.legal_name)
    pic.evidence_ids = sorted(evidence)
    return pic


def resolve_with_index(alert, ix):
    """-> (link, [CustomerPicture], note)"""
    link, holders = _linked_holders(alert, ix)
    note = ""
    if link != "none":
        holders = [h for h in dict.fromkeys(holders) if h in ix.cust]
        if len(holders) > 1:
            fit = [h for h in holders if fits(alert, h, ix)]
            if fit:
                note = "joint_holders_filtered" if len(fit) < len(holders) else "joint_holders_all_fit"
                holders = fit
            else:
                note = "joint_holders_none_fit"
        cand = holders
    else:
        cand = _candidates_by_search(alert, ix)
        link = "search" if cand else "none"
        note = "" if cand else "no_candidates"
    roots = sorted({ix.uf.find(c) for c in cand})
    pictures = [build_picture(r, ix) for r in roots]
    for p in pictures:
        p.link = link
        p.trace.append(f"link={link}" + (f" note={note}" if note else ""))
    return link, pictures, note


def resolve(alert, data):
    """Pipeline entry point: the distinct people that fit this alert."""
    ix = getattr(data, "_customer_index", None)
    if ix is None:
        ix = build_index(tables_from(data))
        data._customer_index = ix
    return resolve_with_index(alert, ix)[1]


def tables_from(data):
    C.use_variants(data.variants)
    t = data.tables
    return {"alerts": t["alerts"], "customers": t["customers"], "accounts": t["accounts"],
            "onboarding": t["onboarding"], "kyc": t["kyc_checks"], "notes": t["notes"]}
