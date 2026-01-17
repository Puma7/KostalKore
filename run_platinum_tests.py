#!/usr/bin/env python3
"""Run Platinum-compatible tests from the Tests folder."""

import sys
import os
import subprocess
from pathlib import Path

def check_platinum_compatibility():
    """Check if Platinum implementation is available."""
    platinum_dir = Path("Kostal Inverter HA")
    
    if not platinum_dir.exists():
        print("❌ Platinum implementation not found")
        return False
    
    # Check key Platinum files
    required_files = [
        "__init__.py",
        "coordinator.py",
        "sensor.py",
        "discovery.py",
        "config_flow.py"
    ]
    
    for file in required_files:
        if not (platinum_dir / file).exists():
            print(f"❌ Missing Platinum file: {file}")
            return False
    
    print("✅ Platinum implementation found")
    return True

def run_basic_tests():
    """Run basic Platinum compatibility tests."""
    print("\n🧪 Running basic Platinum compatibility tests...")
    
    try:
        # Import and test basic Platinum features
        sys.path.insert(0, str(Path("Kostal Inverter HA")))
        
        from kostal_plenticore.coordinator import RequestCache
        from kostal_plenticore.discovery import KostalDeviceScanner
        
        # Test RequestCache
        cache = RequestCache(ttl_seconds=1.0)
        cache.set("test", "value")
        result = cache.get("test")
        assert result == "value"
        print("✅ RequestCache working")
        
        # Test DeviceScanner
        scanner = KostalDeviceScanner(None)
        assert scanner is not None
        print("✅ KostalDeviceScanner working")
        
        return True
        
    except Exception as e:
        print(f"❌ Basic tests failed: {e}")
        return False

def run_pytest_tests():
    """Run pytest tests from Tests folder."""
    print("\n🧪 Running pytest tests...")
    
    tests_dir = Path("Tests")
    if not tests_dir.exists():
        print("❌ Tests directory not found")
        return False
    
    # Check if pytest is available
    try:
        import pytest
        print(f"✅ pytest {pytest.__version__} available")
    except ImportError:
        print("❌ pytest not available")
        return False
    
    # Run pytest with Platinum configuration
    try:
        # Set up environment for Platinum tests
        env = os.environ.copy()
        env['PYTHONPATH'] = str(Path("Kostal Inverter HA"))
        
        # Run pytest
        cmd = [
            sys.executable, "-m", "pytest",
            "Tests/",
            "-v",
            "--tb=short",
            "--maxfail=5",
            "-x"  # Stop on first failure
        ]
        
        print(f"Running: {' '.join(cmd)}")
        
        result = subprocess.run(
            cmd,
            cwd=".",
            env=env,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )
        
        print("STDOUT:")
        print(result.stdout)
        
        if result.stderr:
            print("STDERR:")
            print(result.stderr)
        
        if result.returncode == 0:
            print("✅ All pytest tests passed")
            return True
        else:
            print(f"❌ pytest tests failed with exit code {result.returncode}")
            return False
            
    except subprocess.TimeoutExpired:
        print("❌ pytest tests timed out")
        return False
    except Exception as e:
        print(f"❌ Error running pytest: {e}")
        return False

def run_specific_platinum_tests():
    """Run specific Platinum feature tests."""
    print("\n🧪 Running Platinum-specific tests...")
    
    try:
        # Import and run Platinum-specific tests
        sys.path.insert(0, str(Path("Tests")))
        
        # Test Platinum fixtures
        import conftest
        print("✅ Platinum conftest loaded")
        
        # Test Platinum features
        from conftest import mock_request_cache, mock_device_scanner
        
        cache = mock_request_cache()
        scanner = mock_device_scanner()
        
        assert cache is not None
        assert scanner is not None
        print("✅ Platinum fixtures working")
        
        return True
        
    except Exception as e:
        print(f"❌ Platinum-specific tests failed: {e}")
        return False

def check_test_coverage():
    """Check test coverage for Platinum features."""
    print("\n📊 Checking test coverage...")
    
    tests_dir = Path("Tests")
    platinum_tests = [
        "test_platinum_features.py",
        "test_coordinator_platinum.py"
    ]
    
    found_tests = []
    for test_file in platinum_tests:
        test_path = tests_dir / test_file
        if test_path.exists():
            found_tests.append(test_file)
            print(f"✅ Found: {test_file}")
        else:
            print(f"❌ Missing: {test_file}")
    
    # Check existing tests
    existing_tests = [
        "test_config_flow.py",
        "test_sensor.py",
        "test_diagnostics.py",
        "test_helper.py"
    ]
    
    for test_file in existing_tests:
        test_path = tests_dir / test_file
        if test_path.exists():
            print(f"✅ Found existing: {test_file}")
        else:
            print(f"❌ Missing existing: {test_file}")
    
    print(f"✅ Found {len(found_tests)} Platinum-specific tests")
    return len(found_tests) > 0

def main():
    """Main test runner."""
    print("🚀 Platinum Tests Runner")
    print("=" * 50)
    
    # Check Platinum compatibility
    if not check_platinum_compatibility():
        print("\n❌ Platinum compatibility check failed")
        return False
    
    # Run basic tests
    if not run_basic_tests():
        print("\n❌ Basic tests failed")
        return False
    
    # Check test coverage
    check_test_coverage()
    
    # Run Platinum-specific tests
    if not run_specific_platinum_tests():
        print("\n❌ Platinum-specific tests failed")
        return False
    
    # Run pytest tests
    if not run_pytest_tests():
        print("\n❌ pytest tests failed")
        return False
    
    print("\n" + "=" * 50)
    print("🎉 All Platinum tests passed!")
    print("\n📋 Summary:")
    print("✅ Platinum implementation compatible")
    print("✅ Basic functionality working")
    print("✅ Platinum features tested")
    print("✅ pytest tests passing")
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
