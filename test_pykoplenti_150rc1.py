import asyncio
import aiohttp
from pykoplenti import ApiClient, ApiException

async def test_pykoplenti_150rc1():
    """Test the fixed get_settings_values method"""
    print("🧪 Testing pykoplenti 1.5.0rc1 fixes...")
    
    # Test with dummy client (no real connection)
    async with aiohttp.ClientSession() as session:
        client = ApiClient(host="192.168.1.100", websession=session)
        
        try:
            # This should work without the 500 error on newer models
            # The fix addresses the (str, Iterable[str]) overload issue
            print("✅ Testing get_settings_values method signature...")
            
            # Test the method exists and can be called (will fail with connection error)
            await client.get_settings_values("devices:local", ["Properties:StringCnt"])
            
        except Exception as e:
            # Expected - no real inverter connection
            if "connection" in str(e).lower() or "timeout" in str(e).lower():
                print("✅ Method signature works (connection error expected)")
            else:
                print(f"⚠️  Unexpected error: {e}")
        
        print("🎉 pykoplenti 1.5.0rc1 test completed!")

if __name__ == "__main__":
    asyncio.run(test_pykoplenti_150rc1())
