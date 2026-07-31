# Stock research pipeline. `make` alone lists the targets.
#
# The usual thing is `make run`: pick the next few tickers, research them,
# score them, show what to fix, and rank the whole portfolio. Everything
# else in here is one piece of that.

SCRIPTS := .github/scripts
STATE   := .github/state
SCREEN  := .claude/skills/screen-investments/screen.py

# Tickers per `make run`, and how many at once. Concurrency is bounded by
# the Claude rate limit, not CPU -- 2 is safe, 4 is aggressive.
# Override: make run N=8 J=4
N ?= 4
J ?= 2
TOP ?= 15

.DEFAULT_GOAL := help
.PHONY: help run digest status screen research facts evals evals-all \
        cost gaps ledger ledger-backfill queue-prune

help: ## Show this help
	@echo "Usage: make <target> [TICKER=XYZ] [N=4] [J=2]"
	@echo
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | \
	  awk -F':.*## ' '{printf "  \033[1m%-16s\033[0m %s\n", $$1, $$2}'

## --------------------------------------------------------------------------
## The whole process
## --------------------------------------------------------------------------

run: ## Research N tickers, score, report fixes, show the leaderboard (N=4 J=2)
	@echo "==> baseline (so the cost delta is measurable afterwards)"
	@python3 $(SCRIPTS)/cost_report.py --baseline $(STATE)/cost_baseline.json \
	  >/dev/null 2>&1 || true
	@echo "==> researching $(N) ticker(s), $(J) at a time"
	@$(SCRIPTS)/run_loop.sh -n $(N) -j $(J)
	@echo
	@echo "==> scoring"
	@python3 $(SCRIPTS)/run_evals.py --all >/dev/null 2>&1 || true
	@echo
	@$(MAKE) --no-print-directory digest
	@echo
	@$(MAKE) --no-print-directory screen

digest: ## Cost + quality + suggested fixes for the last batch
	@python3 $(SCRIPTS)/after_run.py

screen: ## Rank every ticker by upside to weighted IV, at live prices
	@echo "=================================================================="
	@echo " SCREENER LEADERBOARD"
	@echo "=================================================================="
	@python3 $(SCREEN) --live --top $(TOP) \
	  --json $(STATE)/last_screen.json 2>/dev/null \
	  || python3 $(SCREEN) --top $(TOP)

status: ## What is researched, what is stale, what is queued
	@python3 $(SCRIPTS)/select_ticker.py 2>&1 | grep -E '^(ticker|mode)=' | sed 's/^/  next  /'
	@echo "  researched   $$(ls -d */Reports 2>/dev/null | wc -l | tr -d ' ') tickers"
	@echo "  scorecards   $$(ls $(STATE)/scores/*.json 2>/dev/null | wc -l | tr -d ' ')"
	@echo "  ledger rows  $$(wc -l < .github/evals/ledger.jsonl 2>/dev/null | tr -d ' ')"

## --------------------------------------------------------------------------
## Pieces
## --------------------------------------------------------------------------

research: ## Research one ticker with live progress (TICKER=AGL.NZ)
	@test -n "$(TICKER)" || { echo "usage: make research TICKER=AGL.NZ" >&2; exit 2; }
	@$(SCRIPTS)/run_local.sh $(TICKER)

facts: ## Rebuild the DuckDB facts table for one ticker (fast, no model)
	@test -n "$(TICKER)" || { echo "usage: make facts TICKER=AGL.NZ" >&2; exit 2; }
	python3 $(SCRIPTS)/build_facts.py $(TICKER) --show

evals: ## Tier-1 eval for one ticker (TICKER=AGL.NZ)
	@test -n "$(TICKER)" || { echo "usage: make evals TICKER=AGL.NZ" >&2; exit 2; }
	python3 $(SCRIPTS)/run_evals.py $(TICKER)

evals-all: ## Tier-1 eval for every ticker; scorecards to .github/state/scores/
	python3 $(SCRIPTS)/run_evals.py --all

cost: ## Per-ticker cost report from run transcripts
	python3 $(SCRIPTS)/cost_report.py

gaps: ## What the extractor could not parse (the improvement backlog)
	python3 $(SCRIPTS)/log_gap.py --report

ledger: ## Append TICKER's current DCF to the prediction ledger
	@test -n "$(TICKER)" || { echo "usage: make ledger TICKER=AGL.NZ" >&2; exit 2; }
	python3 $(SCRIPTS)/ledger.py append $(TICKER)

ledger-backfill: ## Log every existing DCF.json to the ledger (idempotent)
	python3 $(SCRIPTS)/ledger.py backfill

queue-prune: ## Drop delisted tickers from the queue (no model, costs nothing)
	python3 $(SCRIPTS)/prune_queue.py
