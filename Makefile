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
	ruff check crossfire/ tests/
	ruff format --check crossfire/ tests/
	mypy crossfire/

format:
	ruff check --fix crossfire/ tests/
	ruff format crossfire/ tests/

demo:
	crossfire demo --fixture auth_bypass_regression

demo-github:
	crossfire analyze-pr --repo owner/repo --pr 1

eval:
	@echo "Running evaluation harness on fixture PRs..."
	@for fixture in tests/fixtures/prs/*/; do \
		echo "Evaluating $$(basename $$fixture)..."; \
		crossfire demo --fixture $$(basename $$fixture); \
	done

cost-estimate:
	@echo "Cost estimation not yet implemented"
