"""Basic tests to verify test infrastructure is working."""

import pytest


def test_basic_import():
    """Test that we can import basic modules."""
    import asyncio
    from datetime import datetime
    assert True


def test_pytest_working():
    """Test that pytest is working correctly."""
    assert 1 + 1 == 2


@pytest.mark.asyncio
async def test_async_working():
    """Test that async tests are working."""
    await asyncio.sleep(0.01)
    assert True


class TestBasicClass:
    """Test basic class functionality."""
    
    def test_method(self):
        """Test basic method."""
        assert True
    
    @pytest.mark.asyncio
    async def test_async_method(self):
        """Test async method."""
        assert True
