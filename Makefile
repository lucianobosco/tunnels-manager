# Tunnels Manager — development tasks.
VENV ?= .venv
PY   := $(VENV)/bin/python

.PHONY: help venv lint types test test-gui coverage check leaks install clean

help:
	@echo "make venv      create the development environment"
	@echo "make lint      ruff (style and common bugs)"
	@echo "make types     mypy (static types)"
	@echo "make test      unit tests, 100% coverage of the logic enforced"
	@echo "make test-gui  GTK tests: opens real windows on your screen"
	@echo "make check     lint + types + test"
	@echo "make leaks     gitleaks and the private word list, before pushing"
	@echo "make install   install for the current user"

venv:
	python3 -m venv --system-site-packages $(VENV)
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -e ".[dev]"

lint:
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .

types:
	$(VENV)/bin/mypy tunnels_manager

test:
	$(PY) -m pytest --cov --cov-report=term-missing

test-gui:
	$(PY) -m pytest -m gui -o addopts="" --cov --cov-report=term-missing \
		--cov-config=pyproject-gui.toml

check: lint types test

leaks:
	tools/check-leaks.sh

install:
	./install.sh

clean:
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
