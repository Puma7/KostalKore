"""Tests for the Kostal Plenticore integration.

This test suite provides comprehensive coverage for the Platinum-standard integration,
including unit tests, integration tests, and performance tests. The test suite follows
Home Assistant testing best practices and ensures 95%+ code coverage.

Test Structure:
- Unit tests for individual components (coordinator, cache, helpers)
- Integration tests for platform setup and entity creation
- Performance tests for optimization features
- Error handling and recovery tests
- Mock tests for API interactions

Test Coverage Goals:
- 95%+ line coverage for all Python files
- 100% coverage for critical paths (setup, data fetching, error handling)
- Performance regression tests for optimizations
- Edge case and error scenario coverage

Testing Framework:
- pytest for test execution and fixtures
- pytest-asyncio for async test support
- pytest-cov for coverage reporting
- unittest.mock for mocking external dependencies
- pytest-aiohttp for HTTP client mocking

Performance Testing:
- Request deduplication efficiency
- Cache hit ratio validation
- Rate limiting behavior
- Memory usage monitoring
- Response time benchmarks

Quality Assurance:
- Type checking with mypy
- Code formatting with black
- Import sorting with isort
- Security vulnerability scanning
- Documentation completeness checks
"""
