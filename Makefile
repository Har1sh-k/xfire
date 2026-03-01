.PHONY: setup test test-unit test-integration lint demo demo-github eval cost-estimate

setup:
	pip install -e ".[dev]"

test:
	pytest tests/ -v

test-unit:
	pytest tests/unit/ -v

test-integration:
	pytest tests/integration/ -v

lint:
	ruff check xfire/ tests/
	ruff format --check xfire/ tests/
	mypy xfire/

format:
	ruff check --fix xfire/ tests/
	ruff format xfire/ tests/

demo:
	xfire demo --ui

demo-github:
	xfire analyze-pr --repo owner/repo --pr 1

eval:
	@echo "Running evaluation harness on fixture PRs..."
	@for fixture in tests/fixtures/prs/*/; do \
		echo "Evaluating $$(basename $$fixture)..."; \
		xfire demo --fixture $$(basename $$fixture); \
	done

cost-estimate:
	@echo "Cost estimation not yet implemented"
