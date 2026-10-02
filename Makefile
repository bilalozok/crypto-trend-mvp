.PHONY: install install-dev run test lint format check ci precommit-install precommit-run

install:
	python -m pip install --upgrade pip
	pip install -r requirements.txt

install-dev:
	python -m pip install --upgrade pip
	pip install -r requirements-dev.txt

run:
	uvicorn app.main:app --reload --port 8010

test:
	python -m pytest tests -q -ra

lint:
	ruff check .
	black --check .

format:
	ruff check . --fix
	black .

check: lint test

ci: install-dev check

precommit-install:
	pre-commit install

precommit-run:
	pre-commit run --all-files
