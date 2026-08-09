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
LEADERBOARD ?= 15   # rows shown by `make screen`

.DEFAULT_GOAL := help
.PHONY: help run digest status screen integrity missing research facts evals evals-all \
        cost gaps exchange-eval facts-xbrl screen-metrics check-currency ledger ledger-backfill queue-prune \
        screen-fundamentals backfill-units \
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
	@# TICKER=X researches just that one; otherwise take the next TICKERS
	@# from the queue. Without this, `make run TICKER=X` would silently
	@# ignore the argument and research something else entirely.
ifdef TICKER
	@echo "==> researching $(TICKER)"
	@$(SCRIPTS)/run_loop.sh -j 1 $(TICKER)
else
	@echo "==> researching $(TICKERS) ticker(s), $(PARALLEL) at a time"
	@$(SCRIPTS)/run_loop.sh -n $(TICKERS) -j $(PARALLEL)
endif
	@echo
	@echo "==> scoring"
	@$(PY) $(SCRIPTS)/run_evals.py --all >/dev/null 2>&1 || true
	@echo
	@$(MAKE) --no-print-directory digest
	@echo
	@$(MAKE) --no-print-directory screen

digest: ## Cost + quality + suggested fixes for the last batch
	@$(PY) $(SCRIPTS)/after_run.py

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

facts: ## Rebuild the DuckDB facts table for one ticker (fast, no model)
	@test -n "$(TICKER)" || { echo "usage: make facts TICKER=AGL.NZ" >&2; exit 2; }
	$(PY) $(SCRIPTS)/build_facts.py $(TICKER) --show

facts-xbrl: ## Structured extraction for a US filer via SEC XBRL (TICKER=PYPL)
	@test -n "$(TICKER)" || { echo "usage: make facts-xbrl TICKER=PYPL" >&2; exit 2; }
	$(PY) $(SCRIPTS)/build_facts_xbrl.py $(TICKER) --show

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

queue-prune: ## Drop delisted tickers from the queue (no model, costs nothing)
	$(PY) $(SCRIPTS)/prune_queue.py
