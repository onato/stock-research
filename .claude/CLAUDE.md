# Stock Research Project

## Directory Structure
Each ticker gets its own folder:
- `./{TICKER}/PDFs/` - Downloaded PDF reports
- `./{TICKER}/Extracted/` - Text extracted from PDFs
- `./{TICKER}/Reports/` - CSV metrics and HTML dashboard

Example:
- `./SEK.NZ/PDFs/SEK.NZ_Annual_FY2024.pdf`
- `./SEK.NZ/Extracted/SEK.NZ_Annual_FY2024.txt`
- `./SEK.NZ/Reports/SEK.NZ_Metrics.csv`
- `./SEK.NZ/Reports/SEK.NZ_Dashboard.html`

## File Naming Convention
{TICKER}_{REPORT_TYPE}_{PERIOD}.pdf

Report types: Annual, HalfYear, Quarterly, Presentation
Periods: FY2024, H1-2024, Q3-2024, etc.

## Dashboard Data Format
CSV file with headers. First column is always "Period".
Dashboard HTML loads CSV via fetch() and parses with Papa.parse.

## Dependencies
- pdftotext (from poppler-utils): `brew install poppler`
- curl for downloading files
- Web browser to view dashboards

## Reference Dashboards
- WISE.L/Reports/WISE_Dashboard.html
- DUOL/Reports/DUOL_Dashboard.html

## Custom Commands
- `/research-stock {TICKER}` - Full workflow: download reports, extract text, parse metrics, generate dashboard
