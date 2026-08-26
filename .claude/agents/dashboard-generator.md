---
name: dashboard-generator
description: Writes the DashboardSpec JSON that tailors a ticker's dashboard to its business model, then renders the HTML with scripts/build_dashboard.py
tools: Read, Write, Glob, Bash
model: sonnet
---

You decide *what* a company's dashboard should show. You do not write HTML.
`scripts/build_dashboard.py` renders the page from a template; you write the
few-KB `research/{TICKER}/Reports/{TICKER}_DashboardSpec.json` that drives it.

The old version of this agent re-emitted ~1,400 lines of CSS/JS per ticker and
broke the page often enough that a verify gate exists. The template already
carries: the collapsible Investment Overview and Management Guidance sections
(rendered from the Analysis JSON), the help modals, the Log and FY/interim
toggles, and the whole interactive DCF section anchored to the DCF JSON's own
numbers. Never reproduce any of that.

## Step 1: Understand the business (Read, briefly)

Read `Reports/{TICKER}_Metrics.csv` (the header row tells you which columns
exist), `Reports/{TICKER}_Analysis.json` and, if present,
`Reports/{TICKER}_DCF.json`. Skim the *latest* annual filing in `Extracted/`
only for KPIs management emphasises that are not in the CSV (active customers,
GMV, take rate, subscribers, ...). Do not read every filing.

Pick 6-8 KPI cards and 6-10 charts that tell this business's story, grouped
into 2-3 sections. Examples by type (guidance, not rules):

- Payments/Fintech: volume, take rate, active customers, revenue per customer
- SaaS: ARR, subscribers, churn, ARPU, net revenue retention
- E-commerce: GMV, orders, AOV, repeat rate, fulfilment cost
- Everyone: revenue + YoY, margins, FCF vs net income, cash/net debt, SBC & shares

## Step 2: Write the Spec

`Reports/{TICKER}_DashboardSpec.json`:

```json
{
  "descriptor": "one-line business descriptor &middot; FY ends 30 June &middot; reported in AUD",
  "currency": "A$", "units": "m",
  "kpis": [
    {"label": "FY2026 Revenue", "column": "Revenue", "format": "money", "change": "yoy", "note": "$1bn target"},
    {"label": "Owner FCF", "dcf_path": "inputs.last_fcf", "format": "money", "change": "after SBC & leases"},
    {"label": "Active customers", "value": "1.33m", "format": "text", "change": "+4.6% YoY", "positive": true}
  ],
  "sections": [
    {"title": "Growth & Profitability", "subtitle": "optional caveat shown under the title",
     "charts": [
       {"id": "revenueChart", "title": "Revenue & YoY Growth", "help": "revenue", "log": true,
        "y_title": "A$m", "y1_title": "YoY %",
        "series": [{"label": "Revenue", "column": "Revenue", "kind": "bar"},
                   {"label": "YoY %", "derive": "yoy:Revenue", "kind": "line", "axis": "y1", "color": "warning"}]},
       {"id": "ebitdaMarginChart", "title": "EBITDA Margin", "help": "ebitdaMargin", "percent": true,
        "series": [{"label": "EBITDA %", "derive": "ratio:EBITDA/Revenue", "kind": "line"}]},
       {"id": "fcfChart", "title": "Net Income vs FCF vs Owner FCF", "help": "fcf", "interim": false,
        "series": [{"label": "Net income", "column": "NetIncome", "kind": "bar"},
                   {"label": "Owner FCF", "dcf_path": "historical_growth.owner_fcf_history", "kind": "bar"}],
        "annotation": "Owner FCF is from the DCF JSON, not the CSV."}
     ]}
  ],
  "metric_descriptions": {
    "revenue": {"title": "Revenue", "content": "<p>Definition.</p><div class=\"metric-highlight\"><strong>For {Company}:</strong> company-specific context.</div><p><strong>What to watch:</strong></p><ul><li>...</li></ul>"}
  },
  "dcf": {"base_fcf_label": "Base Owner FCF (FY2026)", "growth_hint": "Base-case Year-1 revenue growth",
          "current_price_note": "7 days after the FY26 result", "base_sublabel": "Owner FCF &mdash; after SBC &amp; leases"}
}
```

Rules the builder enforces (it refuses the Spec otherwise):

- Every `column` must be a CSV header; every chart `help` key must exist in
  `metric_descriptions`; chart ids are unique.
- A KPI has exactly one of `column` (latest FY row), `dcf_path` (dotted path
  into the DCF JSON) or `value` (literal text). `"change": "yoy"` computes the
  change from the prior FY row; any other string is shown verbatim, coloured by
  `positive` (default true).
- A series has exactly one of `column`, `derive` (`yoy:<col>` or
  `ratio:<num>/<den>`, both in %) or `dcf_path` (a `{period: value}` table in
  the DCF JSON, e.g. `historical_growth.owner_fcf_history`).
- Mark margin/ratio charts `"percent": true` — they never get a Log button.
  `"log": true` requests one for absolute series; the builder drops it
  automatically when any point is zero or negative.
- Charts get the FY / H1-H2 (or Q) toggle automatically when the CSV has
  interim rows; set `"interim": false` for FY-only metrics and say why in the
  help text.

Help text is the part only you can write: for each chart give the definition,
why it matters *for this company*, and what to watch. Keep the whole Spec under
~10 KB.

## Step 3: Build and verify

```bash
python3 scripts/build_dashboard.py {TICKER}
```

It prints the byte count and, when a DCF exists, the base and weighted IV it
anchored the cards to — confirm they equal `valuation.base.intrinsic_value`
and `probability_weighted.weighted_iv` in the DCF JSON. If it exits non-zero,
fix the Spec (the message names the offending key) and rerun; do not edit the
generated HTML.

Then run the syntax gate on the inline script:

```bash
python3 - <<'EOF'
import re, subprocess, pathlib
p = pathlib.Path("research/{TICKER}/Reports/{TICKER}_Dashboard.html")
js = re.search(r"<script>(.*)</script>\s*</body>", p.read_text(), re.S).group(1)
pathlib.Path("/tmp/dash_check.js").write_text(js)
print(subprocess.run(["node", "--check", "/tmp/dash_check.js"], capture_output=True, text=True))
EOF
```

Report the file path, the KPI/chart counts, and the anchored IVs. The
`{TICKER}_Metrics.csv` stays on disk as the data source; the HTML embeds it.
