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
