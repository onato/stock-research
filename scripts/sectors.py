#!/usr/bin/env python3
"""Route a ticker to a business-model bucket.

The corpus carries 142 distinct free-text sector strings and about 40
spellings of the DCF's model label; neither is a taxonomy. The bucket
decides which default dashboard template applies, so precedence is:

  1. the DCF's own model label -- the valuation agent already decided what
     kind of company this is (AFFO => REIT, NAV => investment vehicle,
     book value / earnings at cost of equity => financial, risked NPV =>
     pre-revenue developer);
  2. keywords in the sector text;
  3. otherwise an ordinary operating company. Unknown is never guessed.
"""

import re
from typing import Any

BUCKETS = ("reit", "lic_nav", "bank", "retirement_ora", "miner_prerevenue",
           "saas", "payments", "ecommerce", "marketplace", "operating")

_MODEL_RULES: list[tuple[str, str]] = [
    (r"\baffo\b|reit", "reit"),
    (r"\bnav\b|net asset value|net tangible assets per unit|nta[- ]based", "lic_nav"),
    ((r"bvps|book[_ -]value|tangible[_ -]book|residual[_ -]income|"
      r"earnings[_ -]based[_ -]cost[_ -]of[_ -]equity|"
      r"distributable[_ -]earnings|dividend[_ -]discount"), "bank"),
    (r"risked|project[- ]npv", "miner_prerevenue"),
    (r"\bora\b|underlying profit.*(village|retirement)|retirement", "retirement_ora"),
]

_SECTOR_RULES: list[tuple[str, str]] = [
    (r"retirement|aged care|villages", "retirement_ora"),
    (r"\breit\b|property trust|real estate|commercial property|office property", "reit"),
    ((r"closed[- ]end fund|investment trust|investment company|listed investment|"
      r"\blic\b|venture capital fund|unit trust"), "lic_nav"),
    ((r"\bbank|lending|lender|deposit taker|insurer|\binsurance\b|thrift|credit union|"
      r"financial services"), "bank"),
    (r"gold|mining|exploration|mineral|developer.*(mine|project)", "miner_prerevenue"),
    (r"marketplace|classifieds|online auction", "marketplace"),
    (r"e-?commerce|online retail|direct marketing retail|internet retail|meal kit", "ecommerce"),
    (r"payment|fintech|remittance|money transfer|card network", "payments"),
    (r"\bsaas\b|software|cloud|subscription|application", "saas"),
]


def dcf_model_label(dcf: dict[str, Any] | None, *, include_prose: bool = True) -> str:
    """Every spelling of the model label the corpus uses, joined.

    `include_prose=False` leaves out valuation_philosophy: that block is
    paragraphs, and an owner-FCF DCF that mentions "distributable" or
    "book value" in passing routed SKC.NZ and STU.NZ to the bank template.
    """
    if not isinstance(dcf, dict):
        return ""
    parts: list[str] = []
    for key in ("model", "model_type", "valuation_model", "methodology", "method"):
        v = dcf.get(key)
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, dict):
            parts.extend(str(x) for x in v.values() if isinstance(x, str))
    inputs = dcf.get("inputs")
    if isinstance(inputs, dict):
        parts.extend(inputs[k] for k in ("model", "valuation_model", "method")
                     if isinstance(inputs.get(k), str))
    vp = dcf.get("valuation_philosophy") if include_prose else None
    if isinstance(vp, str):
        parts.append(vp)
    elif isinstance(vp, dict):
        parts.extend(vp[k] for k in ("model", "model_type", "approach", "method")
                     if isinstance(vp.get(k), str))
    return " | ".join(parts)


def bucket(sector_text: str | None, model_label: str | None) -> str:
    """The template bucket for a ticker. Never raises; unknown => operating.

    Only the sector string is searched for keywords: a company overview
    mentions "insurance gain" (SKC.NZ, a casino) or "mining consumables"
    (SKL.NZ, an industrial) often enough to mis-route.
    """
    label = (model_label or "").lower()
    for pattern, name in _MODEL_RULES:
        if re.search(pattern, label):
            return name
    text = (sector_text or "").lower()
    if not text.strip() or text.strip() in ("none", "unknown"):
        return "operating"
    for pattern, name in _SECTOR_RULES:
        if re.search(pattern, text):
            return name
    return "operating"
