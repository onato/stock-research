# Move ticker directories out of the repo root

Deferred until the LBTYA/SEK.NZ/WISE.L/FLOW.AS batch finishes -- those
four are writing to directories the move would relocate.

## Why

76 ticker dirs in the root today; the queue now holds 1,830. The root is
already hard to scan and will be unusable at that scale.

## Shape (decide before starting)

  research/{TICKER}/          flat, one prefix everywhere -- simplest
  research/{EXCHANGE}/{TICKER}/  browsable, but every script must derive
                                 the exchange from the ticker

## What has to change

Skills and agents -- these matter most, they are what the model follows:
  .claude/skills/research-stock/SKILL.md      writes ./$ARGUMENTS/Reports/
  .claude/agents/financial-parser.md          duckdb ./{TICKER}/Reports/...
  .claude/agents/dcf-analyst.md
  .claude/agents/dashboard-generator.md
  .claude/agents/qualitative-analyst.md
  .claude/skills/screen-investments/screen.py globs */Reports/*_DCF.json

Scripts (mechanical, mostly REPO / ticker):
  scripts/select_ticker.py    has_reports(), pick_stalest() glob
  scripts/build_facts.py      scripts/build_facts_xbrl.py
  scripts/extract.py          scripts/export_csv.py
  scripts/load_existing.py    scripts/run_evals.py
  scripts/screen_metrics.py   scripts/ledger.py
  scripts/exchange_eval.py    Makefile (status target)

Published output -- the expensive part:
  index.html                  68 relative links
  69 dashboards               each embeds its own CSV path
  GitHub Pages URLs           onato.github.io/stock-research/{TICKER}/...
                              every published link changes

## Order

1. Finish the batch; commit everything.
2. Pick the shape (flat vs by-exchange).
3. git mv the directories in one commit so history follows.
4. Update skills + agents, then scripts, then index.html.
5. `make status`, `make evals-all`, `make screen-metrics` must all pass.
6. One real `make run TICKER=X` before trusting it.
