#!/usr/bin/env python3
"""Flag queued tickers whose PRIMARY business is one Stephen won't hold.

Six categories: animal products, weapons, surveillance, tobacco, gambling and
fossil fuels. The rule is what the company mainly does, not any exposure at
all -- a supermarket sells meat and an airline burns jet fuel, and both stay
in.

DELIBERATE NON-GOALS
--------------------
This script does not exclude anything. It writes an `ethics` block into
`research/{T}/info.json` recording which categories matched, the terms that
matched them, a snippet, and a confidence. Promoting a ticker into
`state/never_interested.txt` stays a human decision, because the false
positives here are systematic rather than random: "arms" hides inside
Armstrong, "gun" inside Ginguro, "coal" inside coalition, and "target" belongs
to a retailer at least as often as to a weapon.

It also does not fetch anything. It reads text the repo already has -- the
info.json name/sector/quirks and, where a ticker has been researched, the far
better `overview` and `business_model` from its Analysis.json. A ticker with
no local text is reported as `unknown`, not as clean: the two are different
answers and conflating them is how a defence prime gets a silent pass.

Precision is preferred to recall throughout. A missed company gets caught when
it is researched and its Analysis.json lands; a wrong exclusion silently
removes a good company from a 1,784-name queue that nobody re-reads.

Usage:
    screen_ethics.py --all [--apply]      # every queued ticker
    screen_ethics.py --ticker SAN.NZ      # one
    screen_ethics.py --report             # summary of what is already recorded
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import pathlib
import re
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]

# --------------------------------------------------------------------------
# Vocabulary
#
# Two strengths per category:
#   STRONG  the term alone names the primary business ("abattoir", "casino").
#   WEAK    consistent with the category but also with innocent businesses
#           ("meat" is in "meat processor" and in "meat counter"). A weak hit
#           alone never reaches high confidence.
#
# Every term is matched on word boundaries, which is what keeps Armstrong out
# of `weapons`. Multi-word terms are matched as phrases.
# --------------------------------------------------------------------------
STRONG: dict[str, tuple[str, ...]] = {
    "animal_products": (
        "abattoir", "abattoirs", "slaughterhouse", "slaughterhouses", "meatworks",
        "meat processor", "meat processing", "meat packing", "meatpacking",
        "beef", "lamb", "mutton", "pork", "poultry", "livestock",
        "dairy", "dairies", "infant formula", "milk powder", "cheese",
        "seafood", "fishing", "fisheries", "aquaculture", "salmon farming",
        "fishmeal", "leather", "fur", "wool", "tannery", "eggs",
        "animal testing", "vivisection", "live export",
    ),
    "weapons": (
        "weapons", "weaponry", "armaments", "munitions", "ammunition",
        "firearm", "firearms", "rifle", "rifles", "handgun", "handguns",
        "missile", "missiles", "warhead", "warheads", "torpedo", "torpedoes",
        "artillery", "howitzer", "grenade", "grenades", "explosives",
        "defence contractor", "defense contractor", "defence prime", "defense prime",
        "military aircraft", "fighter aircraft", "combat vehicle", "combat vehicles",
        "armoured vehicle", "armored vehicle", "armoured fighting", "armored fighting",
        "warship", "warships", "submarine", "submarines",
        "aerospace and defence", "aerospace and defense",
    ),
    "surveillance": (
        "facial recognition", "biometric surveillance", "mass surveillance",
        "spyware", "lawful intercept", "interception", "wiretap",
        "predictive policing", "data broker", "data brokerage",
        "private prison", "private prisons", "detention centre", "detention center",
        "detention centres", "detention centers", "immigration detention",
        "correctional facilities", "prison operator",
    ),
    "tobacco": (
        "tobacco", "cigarette", "cigarettes", "cigar", "cigars",
        "vaping", "e-cigarette", "e-cigarettes", "nicotine", "snus",
    ),
    "gambling": (
        "casino", "casinos", "gambling", "sports betting", "sportsbook",
        "wagering", "bookmaker", "bookmakers", "lottery", "lotteries",
        "slot machines", "poker", "igaming", "online gaming licence",
    ),
    "fossil_fuels": (
        "crude oil", "oil and gas", "oil & gas", "petroleum", "refinery",
        "refineries", "oilfield", "oil field", "upstream oil", "downstream oil",
        "thermal coal", "coking coal", "coal mining", "coal mine", "coal mines",
        "natural gas production", "gas exploration", "oil exploration",
        "shale", "offshore drilling", "drilling contractor", "tar sands",
        "oil sands", "lng export", "coal-fired",
    ),
}

WEAK: dict[str, tuple[str, ...]] = {
    "animal_products": ("meat", "milk", "fish", "farming", "protein", "hides"),
    "weapons": ("defence", "defense", "military", "army", "naval", "tactical"),
    "surveillance": ("surveillance", "monitoring", "tracking", "biometric"),
    "tobacco": ("smoking",),
    "gambling": ("betting", "gaming", "wager"),
    "fossil_fuels": ("oil", "gas", "coal", "fuel", "hydrocarbon", "diesel"),
}

# Sector strings are a much stronger signal than prose: they are a
# classification someone already made, not an incidental mention.
SECTOR_PATTERNS: dict[str, tuple[str, ...]] = {
    "animal_products": ("seafood", "fishing", "aquaculture", "dairy", "meat",
                        "livestock", "protein", "agriculture / dairy"),
    "weapons": ("defence", "defense", "aerospace & defence", "aerospace & defense",
                "weapons", "armaments"),
    "surveillance": ("surveillance", "security & surveillance", "corrections"),
    "tobacco": ("tobacco",),
    "gambling": ("gambling", "casino", "casinos", "gaming & casinos", "betting",
                 "lottery"),
    "fossil_fuels": ("oil", "gas", "coal", "petroleum", "energy - fossil",
                     "oil & gas", "oil and gas"),
}

# Phrases that mean the exposure is incidental, and any category hit in the
# same sentence should be demoted. "Deals primarily with" is the user's test,
# so a cost line or one department of many is explicitly not primary.
INCIDENTAL = (
    "one of many", "among other", "amongst other", "one of its many",
    "largest single operating cost", "operating cost", "input cost",
    "counter is one", "department", "also sells", "among its",
    "backed up by", "back-up", "backup", "lending", "loan book", "book includes",
    "supply chain", "customers include", "serves customers",
)

# Roles that SERVE an industry without dealing in its product. This is the
# distinction "deals primarily with" turns on, and it is the single largest
# source of false positives in the real queue: NZX lists dairy DERIVATIVES,
# Skellerup sells rubberware TO dairy farms, MOVe HAULS aquaculture cargo,
# Heartland LENDS against livestock, PaySauce sells PAYROLL to dairy operators.
# None of them own an animal.
#
# A category hit in a sentence that also names one of these roles is demoted:
# the company is a supplier, a venue, a financier or a carrier, not a producer.
SERVICE_ROLES = (
    # venues and marketplaces
    "exchange", "derivatives", "futures", "marketplace", "listing", "clearing",
    "settlement", "index", "brokerage",
    # suppliers and equipment
    "rubberware", "consumables", "equipment", "machinery", "components",
    "packaging", "ingredients supplier", "supplies to", "sold to",
    "manufacturer of engineered", "polymer", "rubber products",
    # movement and storage
    "logistics", "freight", "haulage", "cargo", "shipping", "warehousing",
    "cold storage", "transport", "port",
    # money and software
    "payroll", "saas", "software", "platform for", "fintech", "insurer",
    "finance", "financing", "advisory", "consultancy", "analytics",
    # testing / certification
    "certification", "laboratory", "testing services", "inspection",
)

# A sector that is explicitly none of the above; used to avoid reading a
# passing prose mention as a business line.
BENIGN_SECTORS = ("software", "saas", "edtech", "fintech", "retail", "grocery",
                  "property", "reit", "bank", "insurance", "telecom", "utilities",
                  "healthcare", "pharmaceuticals", "media")


# Patterns matched against the registered NAME only.
#
# For 878 of the queue the name is the only text there is, and it is a strong
# signal: a company called "PetroChina" or "China Coal Energy" is not ambiguous.
# Names are matched more aggressively than prose -- including inside compounds
# ("Petro", "Yancoal"), which would be far too loose in a paragraph.
#
# The brief is recall-first: a missed mismatch wastes a whole research run, a
# wrong skip costs one name out of 1,784.
NAME_PATTERNS: dict[str, tuple[str, ...]] = {
    "animal_products": (
        r"\bdairy\b", r"\bmilk\b", r"\bmeat\b", r"\bbeef\b", r"\bpork\b",
        r"\bpoultry\b", r"\bseafood\b", r"\bfisher(?:y|ies)\b", r"\bfishing\b",
        r"\bsalmon\b", r"\baqua(?:culture)?\b", r"\bagricultural\b",
        r"\bagri\w*\b", r"\blivestock\b", r"\bcattle\b",
    ),
    "weapons": (
        r"\bdefen[cs]e\b", r"\barmaments?\b", r"\bmunitions?\b", r"\bordnance\b",
        r"\barms\b(?!trong)", r"\bweapons?\b", r"\bmissiles?\b",
    ),
    "surveillance": (
        r"\bsurveillance\b", r"\bbiometrics?\b", r"\bcorrections?\b",
    ),
    "tobacco": (r"\btobacco\b", r"\bcigarettes?\b", r"\bvape\b"),
    "gambling": (
        r"\bcasinos?\b", r"\bgaming\b", r"\bbetting\b", r"\blotter(?:y|ies)\b",
        r"\bwager\w*\b",
    ),
    "fossil_fuels": (
        # embedded forms: PetroChina, Yancoal, Sinopec
        r"petro\w*", r"\w*coal\b", r"\boil\b", r"\boilfield\b",
        r"\bpetroleum\b", r"\bgasoline\b", r"\bdrilling\b", r"\brefin\w+\b",
        r"\bshale\b", r"\bhydrocarbons?\b", r"\bsinopec\b",
    ),
}

# "Energy" and "Gas" are far too broad on their own -- they name solar, battery,
# grid and distribution companies. They only count in a name when paired with a
# word that means extraction or combustion.
NAME_QUALIFIED: dict[str, tuple[tuple[str, str], ...]] = {
    "fossil_fuels": ((r"\benergy\b", r"coal|petro|oil|gas field|lng|shale"),),
}

# Names that look like a hit but are not the business. A gas DISTRIBUTOR pipes
# what someone else drilled; a bank lending to farms is a bank.
NAME_EXCLUDE = (
    r"\bbank\b", r"\binsurance\b", r"\bgreen energy\b", r"\bnew energy\b",
    r"\bsmart energy\b", r"\bbattery\b", r"\bbatteries\b", r"\bsolar\b",
    r"\brenewable\b", r"\bhydrogen\b", r"\bwind\b",
    # gas utilities/distributors rather than producers
    r"\btowngas\b", r"\bgas holdings\b", r"\bgas group\b", r"\bgas company\b",
    r"\bresources gas\b", r"\bcity gas\b", r"\bgas transmission\b",
)


def classify_name(name: str) -> dict[str, list[str]]:
    """Categories implied by the registered name alone."""
    if not name:
        return {}
    low = name.lower()
    if any(re.search(p, low) for p in NAME_EXCLUDE):
        return {}
    hits: dict[str, list[str]] = {}
    for cat, pats in NAME_PATTERNS.items():
        found = [p for p in pats if re.search(p, low)]
        if found:
            hits[cat] = found
    for cat, pairs in NAME_QUALIFIED.items():
        for base, qual in pairs:
            if re.search(base, low) and re.search(qual, low):
                hits.setdefault(cat, []).append(base)
    return hits


def _word_re(term: str) -> re.Pattern[str]:
    """Word-boundary matcher; multi-word terms allow flexible whitespace."""
    parts = [re.escape(p) for p in term.split()]
    return re.compile(r"\b" + r"\s+".join(parts) + r"\b", re.IGNORECASE)


_STRONG_RE = {c: [(t, _word_re(t)) for t in terms] for c, terms in STRONG.items()}
_WEAK_RE = {c: [(t, _word_re(t)) for t in terms] for c, terms in WEAK.items()}


def _snippet(text: str, m: re.Match[str], width: int = 90) -> str:
    lo = max(0, m.start() - width // 2)
    hi = min(len(text), m.end() + width // 2)
    return ("..." if lo else "") + text[lo:hi].strip() + ("..." if hi < len(text) else "")


def _sentence_around(text: str, m: re.Match[str]) -> str:
    lo = text.rfind(".", 0, m.start()) + 1
    hi = text.find(".", m.end())
    return text[lo: hi if hi != -1 else len(text)].lower()


def _is_incidental(text: str, m: re.Match[str]) -> bool:
    """Is this match incidental exposure rather than the primary business?

    True when the sentence marks it as one line among many, as a cost, or when
    it names a SERVICE_ROLE -- serving an industry is not dealing in it.
    """
    sentence = _sentence_around(text, m)
    return (any(p in sentence for p in INCIDENTAL)
            or any(r in sentence for r in SERVICE_ROLES))


def classify(text: str) -> dict[str, dict[str, Any]]:
    """Match a free-text business description against the vocabulary.

    Returns {category: {terms, snippet, confidence}}. Confidence is `high` only
    for a strong term in a non-incidental sentence; a weak term alone never
    exceeds `low`, because those words appear in innocent businesses constantly.
    """
    if not text:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for cat in STRONG:
        terms: list[str] = []
        snippet = ""
        strong_hit = False
        for term, rx in _STRONG_RE[cat]:
            m = rx.search(text)
            if not m:
                continue
            terms.append(term)
            if not snippet:
                snippet = _snippet(text, m)
            if not _is_incidental(text, m):
                strong_hit = True
        weak_terms: list[str] = []
        for term, rx in _WEAK_RE[cat]:
            m = rx.search(text)
            if m and not _is_incidental(text, m):
                weak_terms.append(term)
                if not snippet:
                    snippet = _snippet(text, m)
        if not terms and not weak_terms:
            continue
        # A weak term on its own is not evidence of a primary business. It is
        # recorded only when something strong is also present, so that
        # "jet fuel is its largest cost" does not flag an airline.
        if not terms:
            continue
        out[cat] = {
            "terms": terms + weak_terms,
            "snippet": snippet,
            "confidence": "high" if strong_hit else "low",
        }
    return out


def classify_record(rec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Classify a ticker from its sector plus whatever prose we hold.

    The sector is checked separately and outranks prose: it is a label someone
    assigned to the whole company, so "Oil & Gas Exploration" is a primary
    business in a way that the word "oil" in a paragraph is not.
    """
    sector = (rec.get("sector") or "").strip()
    name = rec.get("name", "") or ""
    text = " ".join(x for x in (name, rec.get("text", "")) if x)
    out = classify(text)

    # The name is checked on its own terms: for most of the queue it is the
    # only signal, and it is matched more aggressively than prose.
    for cat in classify_name(name):
        prev: dict[str, Any] = out.get(cat, {"terms": [], "snippet": ""})
        out[cat] = {
            "terms": sorted({*prev["terms"], f"name:{name}"}),
            "snippet": prev["snippet"] or f"name: {name}",
            "confidence": "high",
        }

    low_sector = sector.lower()
    if low_sector and low_sector != "unknown":
        for cat, pats in SECTOR_PATTERNS.items():
            if any(_word_re(p).search(low_sector) for p in pats):
                prev_s: dict[str, Any] = out.get(cat, {"terms": [], "snippet": ""})
                out[cat] = {
                    "terms": sorted({*prev_s["terms"], f"sector:{sector}"}),
                    "snippet": prev_s["snippet"] or f"sector: {sector}",
                    "confidence": "high",
                }
        # A clearly benign sector caps prose-only hits: a software company that
        # mentions diesel generators is not a fossil-fuel business.
        if any(b in low_sector for b in BENIGN_SECTORS):
            for ev in out.values():
                if not any(t.startswith("sector:") for t in ev["terms"]):
                    ev["confidence"] = "low"
    return out


# --------------------------------------------------------------------------
# Repo I/O
# --------------------------------------------------------------------------
def queued_tickers(root: pathlib.Path = ROOT) -> list[str]:
    seen: dict[str, None] = {}
    for f in sorted((root / "queue").glob("*.txt")):
        for raw in f.read_text().splitlines():
            line = raw.split("#")[0].strip()
            if line:
                seen.setdefault(line, None)
    return list(seen)


def gather_text(ticker: str, root: pathlib.Path = ROOT) -> tuple[str, str, str, str]:
    """Return (sector, name, text, evidence) for a ticker from local files only.

    The name comes back separately because classify_record matches it against a
    looser vocabulary than prose -- for 878 queued tickers it is the only text
    there is.
    """
    info_p = root / "research" / ticker / "info.json"
    info: dict[str, Any] = {}
    if info_p.exists():
        try:
            info = json.loads(info_p.read_text())
        except (json.JSONDecodeError, OSError):
            info = {}

    def _flat(v: Any) -> str:
        """quirks is a string on most tickers and a list on a few."""
        if isinstance(v, str):
            return v
        if isinstance(v, (list, tuple)):
            return " ".join(str(x) for x in v)
        if isinstance(v, dict):
            return " ".join(str(x) for x in v.values())
        return ""

    name = _flat(info.get("name"))
    sector = _flat(info.get("sector"))
    parts = [name, _flat(info.get("quirks"))]
    evidence = "name-and-sector" if (name or sector) else ""

    if not name or not sector:
        comp_p = root / "state" / "companies.json"
        if comp_p.exists():
            try:
                comp = json.loads(comp_p.read_text()).get(ticker) or {}
                name = name or _flat(comp.get("name"))
                s = _flat(comp.get("sector"))
                sector = sector or ("" if s == "Unknown" else s)
                if name or sector:
                    parts.append(name)
                    evidence = evidence or "name-and-sector"
            except (json.JSONDecodeError, OSError):
                pass

    ana = root / "research" / ticker / "Reports" / f"{ticker}_Analysis.json"
    if ana.exists():
        try:
            d = json.loads(ana.read_text())
            for key in ("overview", "description"):
                v = d.get(key)
                if isinstance(v, str):
                    parts.append(v)
            bm = d.get("business_model")
            if isinstance(bm, str):
                parts.append(bm)
            elif isinstance(bm, dict):
                parts.extend(str(v) for v in bm.values() if isinstance(v, str))
            evidence = "business-summary"
        except (json.JSONDecodeError, OSError):
            pass

    return sector, name, " ".join(p for p in parts if p), evidence


def write_info(path: pathlib.Path, flags: dict[str, dict[str, Any]],
               evidence: str) -> None:
    """Merge an `ethics` block into info.json, leaving every other field alone.

    `evidence` says how much the classifier actually had to go on, which is
    what separates a name-only guess from a verdict backed by a full business
    summary:

        business-summary  a researched ticker's Analysis.json overview
        name-and-sector   info.json / companies.json name, sector, quirks
        none              nothing local to read

    It is not the filename written to -- an earlier version recorded
    `source: "info.json"` inside info.json, which read as circular nonsense.

    `status` is separate because "nothing to read" is a state, not a kind of
    evidence: a clean result and an unchecked one must stay distinguishable or
    a rerun cannot tell what work is left.
    """
    d: dict[str, Any] = {}
    if path.exists():
        try:
            d = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            d = {}
    d["ethics"] = {
        "flags": sorted(flags),
        "detail": flags,
        "evidence": evidence,
        "status": "unchecked" if evidence == "none" else "checked",
        "checked_at": dt.date.today().isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(d, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true", help="every queued ticker")
    g.add_argument("--ticker", help="one ticker")
    g.add_argument("--report", action="store_true", help="summarise recorded flags")
    p.add_argument("--apply", action="store_true",
                   help="write info.json (default is a dry run)")
    p.add_argument("--min-confidence", choices=("low", "high"), default="low")
    args = p.parse_args(argv)

    if args.report:
        return _report()

    tickers = [args.ticker] if args.ticker else queued_tickers()
    flagged = unknown = clean = 0
    rows: list[tuple[str, str, str, str]] = []
    for t in tickers:
        sector, name, text, evidence = gather_text(t)
        if not text.strip() and not sector and not name:
            unknown += 1
            if args.apply:
                write_info(ROOT / "research" / t / "info.json", {}, evidence="none")
            continue
        res = classify_record({"sector": sector, "name": name, "text": text})
        if args.min_confidence == "high":
            res = {k: v for k, v in res.items() if v["confidence"] == "high"}
        if res:
            flagged += 1
            for cat, ev in sorted(res.items()):
                rows.append((t, cat, ev["confidence"], ", ".join(ev["terms"][:4])))
        else:
            clean += 1
        if args.apply:
            write_info(ROOT / "research" / t / "info.json", res, evidence=evidence or "none")

    for t, cat, conf, terms in sorted(rows, key=lambda r: (r[1], r[2], r[0])):
        print(f"{t:<12} {cat:<16} {conf:<5} {terms}")
    print(f"\nflagged {flagged}   clean {clean}   no-local-text {unknown}"
          f"   of {len(tickers)}")
    if not args.apply:
        print("(dry run -- rerun with --apply to write info.json)")
    return 0


def _report() -> int:
    from collections import Counter
    cats: Counter[str] = Counter()
    ev: Counter[str] = Counter()
    n = 0
    for f in glob.glob(str(ROOT / "research" / "*" / "info.json")):
        try:
            d = json.loads(pathlib.Path(f).read_text())
        except (json.JSONDecodeError, OSError):
            continue
        e = d.get("ethics")
        if not e:
            continue
        n += 1
        ev[e.get("evidence", "?")] += 1
        for c in e.get("flags", []):
            cats[c] += 1
    print(f"info.json files carrying an ethics block: {n}")
    for c, k in cats.most_common():
        print(f"  {c:<18} {k}")
    if ev:
        print("\nevidence the verdict rests on:")
        for e, k in ev.most_common():
            print(f"  {e:<18} {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
