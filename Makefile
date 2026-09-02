# Stock research pipeline. `make` alone lists the targets.
#
# The usual thing is `make run`: pick the next few tickers, research them,
# score them, show what to fix, and rank the whole portfolio. Everything
# else in here is one piece of that.

SCRIPTS := scripts
PY      := uv run python3
STATE   := state
SCREEN  := .claude/skills/screen-investments/screen.py

# How many tickers `make run` researches, and how many at once.
# Concurrency is bounded by the Claude rate limit, not CPU -- 2 is safe,
# 4 is aggressive and may hit the limit (the runner sleeps until it resets).
#
#   make run TICKERS=8 PARALLEL=4
#
TICKERS  ?= 4
PARALLEL ?= 2

# `make run` prioritises tickers with unparsed filings. Tier-2 tickers (stale
# by date, financials already current) are excluded by that, which is why 23
# of them had aged past 45 days. They now route to the cheap /refresh-stock
# path, so REQUIRE_NEW=0 is an inexpensive way to work that backlog down.
REQUIRE_NEW ?= 1
PROFILE_BATCH ?= 150
LEADERBOARD ?= 15   # rows shown by `make screen`

.DEFAULT_GOAL := help
.PHONY: help run digest status screen integrity missing prune-stubs standardize-scale research facts evals evals-all \
        cost gaps exchange-eval facts-xbrl adjudicate fetch-asx dcf-context dashboard kpi-coverage screen-metrics check-currency ledger ledger-backfill queue-prune \
        screen-fundamentals backfill-units canonical-iv sync-portfolio commit-refreshed \
        test test-country lint coverage typecheck

help: ## Show this help
	@echo "Usage: make <target> [TICKER=XYZ] [TICKERS=4] [PARALLEL=2]"
	@echo
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | \
	  awk -F':.*## ' '{printf "  \033[1m%-16s\033[0m %s\n", $$1, $$2}'

## --------------------------------------------------------------------------
## The whole process
## --------------------------------------------------------------------------

run: ## Research the next few tickers, score them, report fixes, then rank everything
	@echo "==> baseline (so the cost delta is measurable afterwards)"
	@$(PY) $(SCRIPTS)/cost_report.py --baseline $(STATE)/cost_baseline.json \
	  >/dev/null 2>&1 || true
	@# Free numeric write-back first. A stale price does not invalidate a
	@# valuation -- weighted_iv and entry_price are price-independent -- so
	@# refreshing the derived upsides here keeps tickers off the ~$$6
	@# research path when only the market moved.
	@echo "==> refreshing drifted prices (free, no model)"
	@$(MAKE) --no-print-directory refresh-price APPLY=1 || true
	@# refresh-price rewrites EVERY drifted ticker, but the run only commits
	@# the one it researches (commit_ticker stages research/$$TICKER alone), so
	@# the rest stayed dirty indefinitely -- 133 DCFs were uncommitted on
	@# 2026-09-01, some refreshed as far back as 2026-08-20. Commit them here,
	@# where they are written.
	@$(MAKE) --no-print-directory commit-refreshed || true
	@echo
	@# The selector reads the portfolio tracker live; this keeps the
	@# committed fallback (used by CI, which lacks the sibling repo) current.
	@$(MAKE) --no-print-directory sync-portfolio
	@# TICKER=X researches just that one; otherwise take the next TICKERS
	@# from the queue. Without this, `make run TICKER=X` would silently
	@# ignore the argument and research something else entirely.
ifdef TICKER
	@echo "==> researching $(TICKER)"
	@$(SCRIPTS)/run_loop.sh -j 1 $(TICKER)
else
	@echo "==> researching $(TICKERS) ticker(s), $(PARALLEL) at a time"
	@$(SCRIPTS)/run_loop.sh -n $(TICKERS) -j $(PARALLEL) \
	  $(if $(filter 1,$(REQUIRE_NEW)),--require-new-filings,)
endif
	@echo
	@echo "==> scoring"
	@$(PY) $(SCRIPTS)/run_evals.py --all >/dev/null 2>&1 || true
	@echo
	@$(MAKE) --no-print-directory digest
	@echo
	@$(MAKE) --no-print-directory screen
	@echo
	@# Fill in names and business summaries for queued tickers that are a bare
	@# symbol, so the ethical screen can see them before one is researched.
	@# Self-committing and resumable; a few seconds once the queue is drained.
	@# Never fails the run -- a rate limit is not a reason to fail a night's
	@# research that already succeeded.
	@$(MAKE) --no-print-directory backfill-profiles-nightly || true

backfill-profiles-nightly: ## One night's batch of profile fetches, committed
	@$(PY) $(SCRIPTS)/backfill_profiles.py --limit $(PROFILE_BATCH) --commit

sync-portfolio: ## Regenerate queue/priority.txt from ../portfolio-tracker (no-op without it)
	@$(PY) $(SCRIPTS)/sync_portfolio_queue.py

digest: ## Cost + quality + suggested fixes for the last batch
	@$(PY) $(SCRIPTS)/after_run.py

refresh-plan: ## What each ticker actually needs: tier 0-3, no model, free
	@$(PY) $(SCRIPTS)/refresh_plan.py $(if $(TICKER),--ticker $(TICKER),--all) \
	  $(if $(TIER),--tier $(TIER),)

commit-refreshed: ## Commit price-only DCF/dashboard rewrites left by refresh-price
	@git add -A -- research state/last_screen.json index.html 2>/dev/null || true
	@if git diff --cached --quiet; then \
	  echo "no refreshed prices to commit"; \
	else \
	  n=$$(git diff --cached --name-only | grep -c '_DCF.json' || true); \
	  git commit -q -m "chore: refresh prices on $$n DCF(s)" \
	    -m "Automated price write-back via make run; no model, no valuation change." \
	  && echo "committed price refresh across $$n DCF(s)"; \
	fi

refresh-price: ## Rewrite price-derived DCF numbers from live quotes (APPLY=1 to write)
	@$(PY) $(SCRIPTS)/refresh_price.py \
	  $(if $(APPLY),--apply,--check) \
	  $(if $(TICKER),--ticker $(TICKER),--all)

screen: ## Rank every ticker by upside to weighted IV, at live prices
	@echo "=================================================================="
	@echo " SCREENER LEADERBOARD"
	@echo "=================================================================="
	@$(PY) $(SCREEN) --live --top $(LEADERBOARD) \
	  --json $(STATE)/last_screen.json --html index.html 2>/dev/null \
	  || $(PY) $(SCREEN) --top $(LEADERBOARD) --html index.html

# Named "integrity", not "coverage": `make coverage` already means test line
# coverage, and the two would be read as the same thing.
integrity: ## Data integrity: how much financial history we have, and the holes
	@$(PY) $(SCRIPTS)/integrity_report.py \
	  --json $(STATE)/integrity.json --html integrity.html

# integrity reports the percentages; this names the actual cells, so a fix
# run can be driven off the output instead of opening CSVs by hand.
missing: ## Which fields are missing on which tickers (JSONL; SCOPE=all, FORMAT=csv)
	@$(PY) $(SCRIPTS)/missing_fields.py \
	  --scope $(or $(SCOPE),core8) --format $(or $(FORMAT),jsonl) \
	  $(if $(TICKER),--ticker $(TICKER),) $(if $(FIELD),--field $(FIELD),) \
	  $(if $(OUT),--out $(OUT),)

# Pre-history rows (PINS FY2016: one comparative-carry equity figure for a
# year the company was private) are not gaps; they cannot be re-extracted.
prune-stubs: ## Delete pre-history rows from the metrics CSVs (WRITE=1 to apply)
	@$(PY) $(SCRIPTS)/prune_stub_rows.py $(if $(WRITE),--write,--check) $(TICKER)

# A number cannot reveal its own scale: AAPL's 416,161 is millions and
# 0285.HK's 179,477 is thousands. Every CSV has to say which it is.
standardize-scale: ## Put every CSV in millions and label Units/Currency (WRITE=1 to apply)
	@$(PY) $(SCRIPTS)/standardize_scale.py $(if $(WRITE),--write,--check) $(TICKER)

status: ## What is researched, what is stale, what is queued
	@$(PY) $(SCRIPTS)/select_ticker.py 2>&1 | grep -E '^(ticker|mode)=' | sed 's/^/  next  /'
	@echo "  researched   $$(ls -d research/*/Reports 2>/dev/null | wc -l | tr -d ' ') tickers"
	@echo "  scorecards   $$(ls $(STATE)/scores/*.json 2>/dev/null | wc -l | tr -d ' ')"
	@echo "  ledger rows  $$(wc -l < evals/ledger.jsonl 2>/dev/null | tr -d ' ')"

## --------------------------------------------------------------------------
## Pieces
## --------------------------------------------------------------------------

research: ## Research one ticker with live progress (TICKER=AGL.NZ)
	@test -n "$(TICKER)" || { echo "usage: make research TICKER=AGL.NZ" >&2; exit 2; }
	@$(SCRIPTS)/run_local.sh $(TICKER)

fetch-asx: ## Download ASX annual/half-year reports deterministically (TICKER=TPW.AX YEARS=2016-2026 [DRY=1])
	@test -n "$(TICKER)" || { echo "usage: make fetch-asx TICKER=TPW.AX YEARS=2016-2026 [DRY=1]" >&2; exit 2; }
	$(PY) $(SCRIPTS)/fetch_asx.py $(TICKER) --years $(or $(YEARS),2016-2026) $(if $(DRY),--dry-run,)

dcf-context: ## Print the DCF agent's inputs for one ticker: price, history pivot, KPIs, component lines (no model)
	@test -n "$(TICKER)" || { echo "usage: make dcf-context TICKER=TPW.AX" >&2; exit 2; }
	$(PY) $(SCRIPTS)/dcf_context.py $(TICKER)

facts: ## Rebuild the DuckDB facts table for one ticker (fast, no model)
	@test -n "$(TICKER)" || { echo "usage: make facts TICKER=AGL.NZ" >&2; exit 2; }
	$(PY) $(SCRIPTS)/build_facts.py $(TICKER) --show

adjudicate: ## Pre-resolve facts into Reports/{T}_Worksheet.md (no model; CHECK=1 grades vs core_metrics)
	@test -n "$(TICKER)" || { echo "usage: make adjudicate TICKER=AGL.NZ [CHECK=1]" >&2; exit 2; }
	$(PY) $(SCRIPTS)/adjudicate.py $(TICKER) $(if $(CHECK),--check,)

facts-xbrl: ## Structured extraction for a US filer via SEC XBRL (TICKER=PYPL)
	@test -n "$(TICKER)" || { echo "usage: make facts-xbrl TICKER=PYPL" >&2; exit 2; }
	$(PY) $(SCRIPTS)/build_facts_xbrl.py $(TICKER) --show

dashboard: ## Render Reports/{T}_Dashboard.html from its DashboardSpec.json (no model)
	@test -n "$(TICKER)" || { echo "usage: make dashboard TICKER=AGL.NZ" >&2; exit 2; }
	$(PY) $(SCRIPTS)/build_dashboard.py $(TICKER)

kpi-coverage: ## Which stored KPIs reach a dashboard (TICKER=X optional; no model)
	$(PY) $(SCRIPTS)/kpi_coverage.py $(TICKER)

evals: ## Tier-1 eval for one ticker (TICKER=AGL.NZ)
	@test -n "$(TICKER)" || { echo "usage: make evals TICKER=AGL.NZ" >&2; exit 2; }
	$(PY) $(SCRIPTS)/run_evals.py $(TICKER)

evals-all: ## Tier-1 eval for every ticker; scorecards to state/scores/
	$(PY) $(SCRIPTS)/run_evals.py --all

test: ## Run the deterministic test suite (gates every commit touching scripts/)
	uv run pytest

# Ruff is version-pinned so local and CI agree; bumping it is a deliberate
# commit (the rule set is pinned in pyproject.toml for the same reason).
lint: ## Ruff + shellcheck; the technical-debt gate
	uv run ruff check .
	shellcheck -S warning $(SCRIPTS)/*.sh

# Mypy and ruff are exact-pinned in pyproject's dev group (the gates must
# only move by deliberate commit); running them inside the uv env lets mypy
# see the real site-packages (duckdb etc.) without stub gymnastics.
typecheck: ## Mypy over scripts/; part of the technical-debt gate
	uv run mypy scripts

# The floor only ratchets up: raise it when coverage rises, never lower it.
coverage: ## Test suite with line coverage over scripts/; fails under the floor
	uv run pytest --cov=$(SCRIPTS) --cov-report=term-missing:skip-covered \
	  --cov-fail-under=95

test-country: ## One country's parser tests (COUNTRY=nzx)
	@test -n "$(COUNTRY)" || { echo "usage: make test-country COUNTRY=nzx" >&2; exit 2; }
	uv run pytest tests/parsers/test_$(COUNTRY).py -v

cost: ## Per-ticker cost report from run transcripts
	$(PY) $(SCRIPTS)/cost_report.py

exchange-eval: ## Extraction coverage per exchange (free, no model calls)
	$(PY) $(SCRIPTS)/exchange_eval.py $(if $(EXCHANGE),--exchange $(EXCHANGE),) $(if $(VERBOSE),--verbose,)

screen-metrics: ## Compare core metrics across tickers, normalized (PERIOD=FY2024)
	$(PY) $(SCRIPTS)/screen_metrics.py --period $(or $(PERIOD),FY2024) $(if $(METRIC),--metric $(METRIC),)

screen-fundamentals: ## Screen on TTM/growth/ROE/D-E/PEG (EXCHANGE=NZX ARGS="--min-roe 0.15")
	$(PY) $(SCRIPTS)/screen_fundamentals.py $(if $(EXCHANGE),--exchange $(EXCHANGE),) \
	  $(if $(SUFFIX),--suffix $(SUFFIX),) $(ARGS)

# A dual-listed DCF writes weighted_iv_hkd/_usd rather than the canonical
# weighted_iv, so screen.py flags NO_IV and drops the row. Selection is by
# quote currency only -- never FX conversion, since an RMB IV over an HKD
# price read as +200% upside.
canonical-iv: ## Name the canonical weighted_iv on dual-currency DCFs (APPLY=1 to write)
	$(PY) $(SCRIPTS)/canonical_iv.py $(if $(TICKER),--ticker $(TICKER),--all) \
	  $(if $(APPLY),--apply,)

backfill-profiles: ## Fetch company name + business summary for unnamed queued tickers (LIMIT=n)
	$(PY) $(SCRIPTS)/backfill_profiles.py $(if $(LIMIT),--limit $(LIMIT),) $(if $(DRY),--dry-run,)

backfill-profiles-status: ## How much of the profile backfill is left
	@$(PY) $(SCRIPTS)/backfill_profiles.py --status

screen-ethics: ## Flag queued tickers on the six ethical exclusions (APPLY=1 to write)
	$(PY) $(SCRIPTS)/screen_ethics.py $(if $(TICKER),--ticker $(TICKER),--all) \
	  $(if $(APPLY),--apply,) $(if $(HIGH),--min-confidence high,)

screen-ethics-report: ## Summary of recorded ethical flags
	@$(PY) $(SCRIPTS)/screen_ethics.py --report

backfill-units: ## Infer missing core_metrics.units from DCF anchors (APPLY=1 to write)
	$(PY) $(SCRIPTS)/backfill_units.py $(if $(APPLY),--apply,) $(if $(TICKER),--ticker $(TICKER),)

check-currency: ## Flag tickers whose recorded currency contradicts their filings
	$(PY) $(SCRIPTS)/check_currency.py $(TICKER)

gaps: ## What the extractor could not parse (the improvement backlog)
	$(PY) $(SCRIPTS)/log_gap.py --report

ledger: ## Append TICKER's current DCF to the prediction ledger
	@test -n "$(TICKER)" || { echo "usage: make ledger TICKER=AGL.NZ" >&2; exit 2; }
	$(PY) $(SCRIPTS)/ledger.py append $(TICKER)

ledger-backfill: ## Log every existing DCF.json to the ledger (idempotent)
	$(PY) $(SCRIPTS)/ledger.py backfill

queue-prune: ## Comment out delisted tickers in the queue (APPLY=1 to write)
	$(PY) $(SCRIPTS)/prune_queue.py $(if $(APPLY),--apply,)
