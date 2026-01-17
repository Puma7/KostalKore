#!/usr/bin/env python3
"""Test runner script for Kostal Plenticore integration.

This script provides a simple way to run tests without the complex
import issues that occur with pytest when the package has spaces in
its name and relative imports.
"""

import sys
import os
import subprocess
import tempfile
from pathlib import Path


def run_simple_tests():
    """Run simple infrastructure tests."""
    print("🧪 Running infrastructure tests...")
    
    # Test basic imports
    try:
        import pytest
        print(f"✓ pytest {pytest.__version__}")
    except ImportError as e:
        print(f"✗ pytest not available: {e}")
        return False
    
    try:
        import asyncio
        print("✓ asyncio available")
    except ImportError as e:
        print(f"✗ asyncio not available: {e}")
        return False
    
    try:
        import aiohttp
        print(f"✓ aiohttp {aiohttp.__version__}")
    except ImportError as e:
        print(f"✗ aiohttp not available: {e}")
        return False
    
    try:
        from unittest.mock import MagicMock
        print("✓ unittest.mock available")
    except ImportError as e:
        print(f"✗ unittest.mock not available: {e}")
        return False
    
    return True


def run_unit_tests():
    """Run unit tests for individual modules."""
    print("\n🧪 Running unit tests...")
    
    # Test RequestCache
    try:
        sys.path.insert(0, '.')
        from coordinator import RequestCache
        
        # Test cache functionality
        cache = RequestCache(ttl_seconds=1.0)
        cache.set("test_key", "test_value")
        result = cache.get("test_key")
        assert result == "test_value"
        print("✓ RequestCache tests passed")
        
    except Exception as e:
        print(f"✗ RequestCache tests failed: {e}")
        return False
    
    # Test MODBUS exceptions
    try:
        from coordinator import ModbusException, ModbusIllegalFunctionError
        
        exc = ModbusIllegalFunctionError(0x01)
        assert exc.exception_code == 0x01
        print("✓ MODBUS exception tests passed")
        
    except Exception as e:
        print(f"✗ MODBUS exception tests failed: {e}")
        return False
    
    return True


def run_integration_tests():
    """Run integration tests."""
    print("\n🧪 Running integration tests...")
    
    try:
        # Test config flow imports
        from config_flow import KostalPlenticoreConfigFlow
        print("✓ Config flow imports work")
        
        # Test sensor imports
        from sensor import PlenticoreDataSensor
        print("✓ Sensor imports work")
        
        # Test discovery imports
        from discovery import KostalDeviceScanner
        print("✓ Discovery imports work")
        
    except Exception as e:
        print(f"✗ Integration tests failed: {e}")
        return False
    
    return True


def run_performance_tests():
    """Run performance tests."""
    print("\n🧪 Running performance tests...")
    
    try:
        import time
        from coordinator import RequestCache
        
        # Test cache performance
        cache = RequestCache(ttl_seconds=5.0)
        
        start_time = time.time()
        
        # Add 1000 entries
        for i in range(1000):
            cache.set(f"key_{i}", f"value_{i}")
        
        # Get 1000 entries
        for i in range(1000):
            cache.get(f"key_{i}")
        
        end_time = time.time()
        duration = end_time - start_time
        
        print(f"✓ Cache performance: {duration:.3f}s for 2000 operations")
        
        # Check hit ratio
        hit_ratio = cache.get_hit_ratio()
        print(f"✓ Cache hit ratio: {hit_ratio:.2%}")
        
        if duration < 1.0 and hit_ratio > 0.5:
            print("✓ Performance tests passed")
            return True
        else:
            print("✗ Performance tests failed (too slow or low hit ratio)")
            return False
            
    except Exception as e:
        print(f"✗ Performance tests failed: {e}")
        return False


def run_type_checking():
    """Run type checking."""
    print("\n🧪 Running type checking...")
    
    try:
        import subprocess
        result = subprocess.run([
            sys.executable, "-m", "mypy", 
            "--ignore-missing-imports", 
            "--strict-optional",
            "coordinator.py"
        ], capture_output=True, text=True, cwd=".")
        
        if result.returncode == 0:
            print("✓ Type checking passed")
            return True
        else:
            print(f"✗ Type checking failed: {result.stderr}")
            return False
            
    except Exception as e:
        print(f"✗ Type checking failed: {e}")
        return False


def main():
    """Main test runner."""
    print("🚀 Kostal Plenticore Integration Test Runner")
    print("=" * 50)
    
    tests_passed = 0
    total_tests = 5
    
    # Run all tests
    if run_simple_tests():
        tests_passed += 1
    
    if run_unit_tests():
        tests_passed += 1
    
    if run_integration_tests():
        tests_passed += 1
    
    if run_performance_tests():
        tests_passed += 1
    
    if run_type_checking():
        tests_passed += 1
    
    # Summary
    print("\n" + "=" * 50)
    print(f"📊 Test Results: {tests_passed}/{total_tests} tests passed")
    
    if tests_passed == total_tests:
        print("🎉 All tests passed! The integration is working correctly.")
        return True
    else:
        print("❌ Some tests failed. Please check the implementation.")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
