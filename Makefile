VENV := .venv
PY := $(VENV)/bin/python3

.PHONY: venv install test test-e2e test-remote-dev test-remote-dev-e2e coverage run lint clean

# Create local venv (avoids PEP 668 on Homebrew/system Python)
venv: $(PY)

$(PY):
	python3 -m venv $(VENV)

# Install dependencies
install: venv
	$(PY) -m pip install -r requirements.txt
	$(PY) -m pip install -e .

# Run the CLI
run: venv
	$(PY) -m cli.main

# Run tests
test: venv
	$(PY) -m pytest tests/

# Measure src coverage and refresh the README badge
coverage: venv
	$(PY) -m pytest tests/ --cov=src --cov-report=term-missing --cov-report=xml \
		--ignore=tests/test_real_e2e_integration.py \
		--ignore=tests/test_real_e2e_storage.py \
		--ignore=tests/test_hetzner_storage.py \
		--ignore=tests/test_workspace_git_clone.py \
		--ignore=tests/test_real_e2e_vpn.py \
		--ignore=tests/test_real_e2e_vpn_tunnel.py
	$(PY) scripts/coverage_badge.py --write

# Run E2E tests with Hetzner integration
test-e2e: venv
	$(PY) test_hetzner_integration.py

# Run remote dev unit tests
test-remote-dev: venv
	$(PY) -m pytest tests/test_remote_dev.py -v

# Run remote dev E2E tests
test-remote-dev-e2e: venv
	$(PY) -m pytest tests/test_workspace_git_clone.py -v -m e2e

# Run linting
lint: venv
	$(PY) -m pylint src/

# Clean build artifacts
clean:
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete

# All-in-one development setup
setup: install clean

# Run all tests
test-all: test test-e2e test-remote-dev test-remote-dev-e2e

# Default target
all: install test lint
