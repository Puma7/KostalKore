# Makefile for Kostal Plenticore Integration Testing
# Provides convenient commands for running tests, coverage, and quality checks

.PHONY: help test test-cov test-unit test-integration test-performance test-all
.PHONY: lint format type-check security clean install-test
.PHONY: coverage-html coverage-xml coverage-report quality-check

# Default target
help:
	@echo "Available commands:"
	@echo "  test          - Run all tests"
	@echo "  test-cov      - Run tests with coverage"
	@echo "  test-unit     - Run unit tests only"
	@echo "  test-integration - Run integration tests only"
	@echo "  test-performance - Run performance tests only"
	@echo "  test-all      - Run all test suites"
	@echo "  lint          - Run code linting"
	@echo "  format        - Format code with black"
	@echo "  type-check     - Run type checking with mypy"
	@echo "  security      - Run security checks"
	@echo "  clean         - Clean test artifacts"
	@echo "  install-test  - Install test dependencies"
	@echo "  coverage-html - Generate HTML coverage report"
	@echo "  coverage-xml  - Generate XML coverage report"
	@echo "  coverage-report - Generate all coverage reports"
	@echo "  quality-check - Run all quality checks"

# Test commands
test:
	pytest tests/ -v

test-cov:
	pytest tests/ --cov=kostal_plenticore --cov-report=term-missing

test-unit:
	pytest tests/ -v -m "unit"

test-integration:
	pytest tests/ -v -m "integration"

test-performance:
	pytest tests/ -v -m "performance"

test-all:
	pytest tests/ -v -m "unit or integration or performance"

# Coverage commands
coverage-html:
	pytest tests/ --cov=kostal_plenticore --cov-report=html
	@echo "Coverage report generated in htmlcov/index.html"

coverage-xml:
	pytest tests/ --cov=kostal_plenticore --cov-report=xml
	@echo "Coverage report generated in coverage.xml"

coverage-report:
	pytest tests/ --cov=kostal_plenticore --cov-report=html --cov-report=xml --cov-report=term-missing
	@echo "Coverage reports generated"

# Quality checks
lint:
	flake8 kostal_plenticore/ tests/ --max-line-length=120 --extend-ignore=E203,W503
	pylint kostal_plenticore/ --disable=C0114,C0115,R0903

format:
	black kostal_plenticore/ tests/ --line-length=120
	isort kostal_plenticore/ tests/ --profile black

type-check:
	mypy kostal_plenticore/ --ignore-missing-imports --strict-optional

security:
	bandit -r kostal_plenticore/ -f json -o bandit-report.json
	safety check --json --output safety-report.json

quality-check: lint type-check security
	@echo "All quality checks completed"

# Setup commands
install-test:
	pip install -r requirements_test.txt

clean:
	rm -rf .pytest_cache/
	rm -rf htmlcov/
	rm -rf .coverage
	rm -rf coverage.xml
	rm -rf bandit-report.json
	rm -rf safety-report.json
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

# Development commands
dev-test: install-test test
dev-quality: install-test quality-check
dev-full: install-test test-all quality-check

# CI/CD commands
ci-test: install-test test-cov
ci-quality: install-test quality-check
ci-all: install-test test-all quality-check

# Performance testing
benchmark:
	pytest tests/ --benchmark-only --benchmark-sort=mean

profile:
	pytest tests/ --profile-svg

# Documentation
docs-test:
	pytest tests/ --doctest-modules kostal_plenticore/

# Specific test runs
test-coordinator:
	pytest tests/test_coordinator.py -v

test-sensor:
	pytest tests/test_sensor.py -v

test-config-flow:
	pytest tests/test_config_flow.py -v

# Quick checks
quick-test:
	pytest tests/ -x -v

quick-cov:
	pytest tests/ --cov=kostal_plenticore --cov-report=term-missing -x

# Parallel testing
parallel-test:
	pytest tests/ -n auto

# Debug testing
debug-test:
	pytest tests/ -v -s --pdb

# Timeout testing
timeout-test:
	pytest tests/ --timeout=300

# Continuous integration
ci: ci-test ci-quality
