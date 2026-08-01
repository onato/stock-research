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
4. The financial-parser agent adjudicates `facts` → the canonical `core_metrics` table
   (fixed cross-ticker schema; company-specific metrics go in `kpis`). DDL and column
   aliases live in `scripts/schema.py`.
5. `python3 scripts/export_csv.py {TICKER}` derives `{TICKER}_Metrics.csv` from
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
`test`, `lint`. Don't invoke scripts ad hoc when a target exists.

## Testing — strict TDD for deterministic code

All code under `scripts/` is deterministic and test-covered (`tests/`, pytest via
`pythonpath=["scripts"]`). Evals grade what the *agents* produce; unit tests guard
the Python that does the extracting, grading and exporting. The rules:

- **Red first.** Any change to `scripts/` starts with a failing test: write it, run
  it, watch it fail, then make it pass. No production edit ships without a covering
  test in the same commit.
- **New exchange support starts from a failing fixture** (see Parser Architecture).
- **`make test` must pass before any commit touching `scripts/`, `tests/`, or
  `pyproject.toml`.** `make lint` is the style gate.
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
