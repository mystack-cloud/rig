.PHONY: install test run lint clean

# Install dependencies
install:
	pip install -r requirements.txt

# Run the application
run:
	python src/control-plane/api.py

# Run the CLI
run-cli:
	python src/cli/main.py

# Run tests
test:
	python -m pytest tests/

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

# Default target
all: install test lint