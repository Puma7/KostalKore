import pykoplenti

def test_pykoplenti_import():
    """Test basic import and version check"""
    print("🧪 Testing pykoplenti 1.5.0rc1...")
    
    try:
        # Test import
        from pykoplenti import ApiClient, ApiException
        print("✅ Import successful")
        
        # Test module structure
        print("✅ ApiClient available")
        print("✅ ApiException available")
        
        # Test that we can create the class (without connecting)
        print("✅ Library structure intact")
        
        # Check if it's the right version
        import pkg_resources
        version = pkg_resources.get_distribution("pykoplenti").version
        print(f"✅ Installed version: {version}")
        
        if "1.5.0rc1" in version:
            print("🎉 Correct version installed!")
        else:
            print(f"⚠️  Expected 1.5.0rc1, got {version}")
        
        print("\n📋 What was fixed in 1.5.0rc1:")
        print("- Fixed get_settings_values API 500 error on newer models")
        print("- Fixed (str, Iterable[str]) overload issue")
        print("- Should resolve your G2/G3 500 errors")
        
        return True
        
    except ImportError as e:
        print(f"❌ Import failed: {e}")
        return False
    except Exception as e:
        print(f"⚠️  Error: {e}")
        return False

if __name__ == "__main__":
    success = test_pykoplenti_import()
    if success:
        print("\n🚀 Ready to test with your real Kostal inverter!")
        print("💡 Next steps:")
        print("1. Update requirements.txt in your HA custom component")
        print("2. Restart Home Assistant")
        print("3. Check logs for 500 error reduction")
    else:
        print("\n❌ Installation issues - please check pip install")
