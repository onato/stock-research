# Stock research pipeline. `make` alone lists the targets.

SCRIPTS := .github/scripts

.DEFAULT_GOAL := help

.PHONY: help evals evals-all ledger ledger-backfill cost

help: ## Show this help
	@echo "Usage: make <target> [TICKER=XYZ]"
	@echo
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | \
	  awk -F':.*## ' '{printf "  \033[1m%-16s\033[0m %s\n", $$1, $$2}'

evals: ## Tier-1 eval for one ticker (make evals TICKER=AGL.NZ)
	@test -n "$(TICKER)" || { echo "usage: make evals TICKER=AGL.NZ" >&2; exit 2; }
	python3 $(SCRIPTS)/run_evals.py $(TICKER)

evals-all: ## Tier-1 eval for every ticker; scorecards to .github/state/scores/
	python3 $(SCRIPTS)/run_evals.py --all

ledger: ## Append TICKER's current DCF to the prediction ledger
	@test -n "$(TICKER)" || { echo "usage: make ledger TICKER=AGL.NZ" >&2; exit 2; }
	python3 $(SCRIPTS)/ledger.py append $(TICKER)

ledger-backfill: ## Log every existing DCF.json to the ledger (idempotent)
	python3 $(SCRIPTS)/ledger.py backfill

cost: ## Per-ticker cost report from run transcripts
	python3 $(SCRIPTS)/cost_report.py
