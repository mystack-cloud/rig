.PHONY: install test test-e2e test-remote-dev test-remote-dev-e2e run lint clean

# Install dependencies
install:
	pip install -r requirements.txt

# Run the application
run:
	python3 src/cli/controller.py

# Run the CLI
run-cli:
	python3 src/cli/main.py

# Run tests
test:
	python3 -m pytest tests/

# Run E2E tests with Hetzner integration
test-e2e:
	python3 test_hetzner_integration.py

# Run remote dev unit tests
test-remote-dev:
	python3 -m pytest tests/test_remote_dev.py -v

# Run remote dev E2E tests
test-remote-dev-e2e:
	python3 -m pytest tests/test_workspace_git_clone.py -v -m e2e

# Run linting
lint:
	pylint src/

# Clean build artifacts
clean:
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete

# Build docker image
docker-build:
	docker build -t control-plane-app .

# Run docker image
docker-run:
	docker run -p 8080:8080 control-plane-app

# All-in-one development setup
setup: install clean

# Run all tests
test-all: test test-e2e test-remote-dev test-remote-dev-e2e

# Default target
all: install test lint