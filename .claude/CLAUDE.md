# Stock Research Project

## Directory Structure
Each ticker gets its own folder under `research/`:
- `research/{TICKER}/PDFs/` - Downloaded PDF reports
- `research/{TICKER}/Extracted/` - Text extracted from PDFs
- `research/{TICKER}/Reports/` - DuckDB, CSV metrics, JSON analyses, and HTML dashboard

Example:
- `research/SEK.NZ/PDFs/SEK.NZ_Annual_FY2024.pdf`
- `research/SEK.NZ/Extracted/SEK.NZ_Annual_FY2024.txt`
- `research/SEK.NZ/Reports/SEK.NZ.duckdb`
- `research/SEK.NZ/Reports/SEK.NZ_Metrics.csv`
- `research/SEK.NZ/Reports/SEK.NZ_Dashboard.html`

## Data Pipeline (DB-first)
1. Download reports → `research/{TICKER}/PDFs/`
2. `pdftotext -layout` → `research/{TICKER}/Extracted/*.txt`
   (US filers skip PDFs: `scripts/build_facts_xbrl.py` pulls typed data from the SEC XBRL API)
3. `scripts/build_facts.py` scans the .txt files and writes every candidate value to the
   `facts` table in `research/{TICKER}/Reports/{TICKER}.duckdb`
4. `scripts/adjudicate.py` (run by `extract.py`; `make adjudicate TICKER=X`) resolves
   what the candidates settle by themselves -- single, unanimous, or corroborated by a
   later filing's comparative column -- and writes `{TICKER}_Worksheet.md` (gitignored):
   a grid of ✓ / ~ / ? / ✗ cells with ranked shortlists and statement line ranges.
   `scripts/sections.py` supplies the ranges. `--check` grades ✓ cells against an
   existing `core_metrics` (94% carried the right number on 5 NZX tickers; units and
   half-year labels stay the agent's call).
5. The financial-parser agent reviews the worksheet → the canonical `core_metrics` table
   (fixed cross-ticker schema; company-specific metrics go in `kpis`). DDL and column
   aliases live in `scripts/schema.py`.
6. `python3 scripts/export_csv.py {TICKER}` derives `{TICKER}_Metrics.csv` from
   `core_metrics`. **Never hand-write the CSV** — the script applies the snake_case →
   CamelCase header mapping and refuses to shrink an existing CSV.

The `.duckdb` files are gitignored: they are local, rebuildable caches. The committed
system of record is the CSV/JSON in `Reports/`. `scripts/load_existing.py` is the one
reverse-direction script — it rebuilds a DB from a legacy CSV (pre-DB tickers).

**Units convention:** the canonical cross-ticker scale is millions of the reporting
currency, applied by the `metrics_normalized` view. Unknown or missing units resolve
to NULL — never an assumed scale (SEK.NZ once read as NZ$411bn revenue from a
defaulted thousands→millions guess; a missing row is obvious, a plausible wrong one
is not).

`scripts/backfill_units.py` recovers a missing `units` by comparing DB values against
the same quantities independently written in `{TICKER}_DCF.json` (`inputs.total_debt`,
`last_fcf`, `shares_outstanding`, `cash`). Two or more anchors must agree unanimously
on one decade; anything less leaves NULL. Dry-run is the default (`make backfill-units`,
`APPLY=1` to write) and it only ever fills NULL rows, so it is idempotent.

**`facts.units_hint` is NOT a units source.** It records the scale printed on the
filing page, but the financial-parser agent already rescaled the value before writing
`core_metrics` — so the hint describes the input, not the stored number. Measured:
2CC.NZ's facts say `thousands` 1758 times over rows that are plainly millions (39.8
for a company doing ~NZ$80m/yr); AGL.NZ, SUM.NZ and OCA.NZ are the same. Using it
reintroduces the SEK.NZ bug.

## Cross-Ticker Screening

`make screen-fundamentals EXCHANGE=NZX ARGS="--min-roe 0.15 --max-de 1"` filters every
researched ticker on TTM, growth, ROE, D/E and PEG. It is a different question from
`make screen`, which ranks by DCF upside from the `_DCF.json` files.

Everything is derived fresh from `metrics_normalized` on each run; there is no cached
screening table. Three rules the derivations exist to enforce:

- **TTM is reconstructed, not assumed.** NZX filers report half-yearly, so a TTM is
  `FY(Y-1) + H1(Y) − H1(Y-1)`, not a sum of four quarters. Where no true TTM is
  available the row falls back to the latest FY and is tagged `FY-BASIS`, excluded
  from PASS unless `--allow-fy-basis`.
- **Never use the `eps` column for anything cross-ticker.** 13 tickers store EPS in
  cents (WISE.L, ANZ.NZ, AIA.NZ, ATM.NZ, EBO.NZ, SPK.NZ, ARG.NZ, AFI.NZ, OCA.NZ,
  SDL.NZ, 9999.HK, MELI) and 5 have a shares-scale bug (AFT.NZ, APL.NZ, DCBO, XPEL,
  FIG). `metrics_normalized` deliberately leaves per-share figures unscaled. Derive
  EPS as `ttm_net_income / shares_outstanding` instead.
- **A price must be denominated like the financials it divides.** WISE.L quotes GBP
  pence against USD filings, and `885.6 / 48.43` yields a plausible P/E of 18.3 that
  is pure coincidence. Such rows are refused with `price-currency-mismatch`.

PEG uses the DCF's `historical_growth.selected_growth_rate` (stored as a **percent**)
as a forward proxy — it is not an analyst estimate, and the output says so.

A filing's period is read from its own "for the year / six months ended <date>"
statement (`parsers.common.period_from_text`), against the fiscal-year-end month
`build_facts.py` learns from the folder's annual reports; the filename is only the
fallback. Filenames were one fiscal year out for June/March year-ends (0016.HK's
`H1-2024` is H1 FY2025), which was the "period-shift" class of worksheet error.

Period labels come in 13 format families across 2,274 labels; `scripts/periods.py` is
the single parser. `H1 2026`, `H1-2026`, `H1 FY2026` and `H1-FY2026` all name the same
six months of **fiscal** 2026. Do not hand-roll period parsing — not even
`startswith("FY")`: `FY2017-15mo` and `FY2018-6moStub` (ARB.NZ) are FY-shaped but not
years, which is what `periods.is_annual` exists to say. `export_csv.sort_key` delegates
to `periods.sort_key` (an older token-loop version mis-sorted `Q# FY####`; fixed in
1ff31759).

## Parser Architecture (open/closed)

`scripts/build_facts.py` is only the CLI facade. Parsing lives in `scripts/parsers/`:
`common.py` holds the exchange-independent metric vocabulary and number grammar;
`base.py` holds the scan driver plus strategy hooks and is the generic fallback;
one module per exchange (`nzx.py`, `hkex.py`, `lse.py`, `euronext.py`) registers
itself by listing suffix. Parsers emit raw fact *candidates* with units/currency
hints only — never scale units, never pick winners; adjudication into
`core_metrics` belongs to the financial-parser agent (see the DELIBERATE
NON-GOALS docstring in build_facts.py).

**Adding an exchange = three new files, zero shared-code edits:**
`tests/fixtures/extracted/{country}/` (trimmed real excerpts) +
`tests/parsers/test_{country}.py` (written first, failing) +
`scripts/parsers/{country}.py` (makes it green). Never bend `common.py`/`base.py`
around one exchange's quirk.

## Entry Points

`make` is the interface — `run`, `research`, `facts`, `evals`, `screen`, `status`,
`test`, `lint`, `typecheck`, `coverage`. Don't invoke scripts ad hoc when a target
exists.

## Commits

Follow [Conventional Commits](https://www.conventionalcommits.org): `type: description`
(`feat:`, `fix:`, `test:`, `refactor:`, `docs:`, `chore:`, `style:`). Do **not** use the
optional parenthesized scope — `fix: currency detection`, never `fix(parsers): ...`.

## Testing — strict TDD for deterministic code

All code under `scripts/` is deterministic and test-covered (`tests/`, pytest via
`pythonpath=["scripts"]`). Evals grade what the *agents* produce; unit tests guard
the Python that does the extracting, grading and exporting. The rules:

- **Red first.** Any change to `scripts/` starts with a failing test: write it, run
  it, watch it fail, then make it pass. No production edit ships without a covering
  test in the same commit.
- **New exchange support starts from a failing fixture** (see Parser Architecture).
- **`make test` must pass before any commit touching `scripts/`, `tests/`, or
  `pyproject.toml`.** `make lint` is the style gate; `make typecheck` (mypy,
  every def in `scripts/` annotated) must also pass for those commits.
- **Parser changes additionally require a corpus diff review:**
  `python3 tests/tools/corpus_snapshot.py --out state/facts_before.jsonl` before,
  again after, then diff. Refactors must diff empty; bug fixes must diff only in
  the targeted exchange (`--suffix NZ`) and be reviewed line-by-line.
- Test fixtures are committed trimmed excerpts under `tests/fixtures/` — tests must
  never read live `research/` files (they are overwritten on every re-research).

## File Naming Convention
{TICKER}_{REPORT_TYPE}_{PERIOD}.pdf

Report types: Annual, HalfYear, Quarterly, Presentation
Periods: FY2024, H1-2024, Q3-2024, etc.

## Dashboard Data Format
CSV file with headers. First column is always "Period".
Generated dashboards embed the CSV content inline (a `csvData` template literal copied
from `{TICKER}_Metrics.csv`) so they open from `file://` without a server. The CSV on
disk remains the source — regenerating the dashboard re-embeds it.

## Dependencies
- pdftotext (from poppler-utils): `brew install poppler`
- duckdb CLI: `brew install duckdb`
- curl for downloading files
- Web browser to view dashboards

## Reference Dashboards
- research/WISE.L/Reports/WISE.L_Dashboard.html
- research/DUOL/Reports/DUOL_Dashboard.html

## Custom Commands
- `/research-stock {TICKER}` - Full workflow: download reports, extract text, parse metrics, generate dashboard
