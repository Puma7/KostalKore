
import asyncio
import logging
import sys
import json
from pykoplenti import PlenticoreClient, DefaultUpdateCoordinator

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    # You will need to fill in your IP and password
    IP_ADDRESS = "192.168.1.250" 
    # Try to grab password from config if possible, otherwise user might need to edit this
    # verifying available settings keys is the goal.
    
    print(f"--- Kostal Plenticore Settings Check ---")
    print(f"Connecting to {IP_ADDRESS}...")
    
    # Note: We can't easily get the password from here safely without user input
    # prompting user is safer if they run it manually
    
    print("\nThis script requires your Inverter Password (Installateur/Service Code preferred).")
    password = input("Enter Password: ")
    
    async with PlenticoreClient(IP_ADDRESS, 80, password) as client:
        try:
            print("Authenticating...")
            await client.login()
            print("Successfully authenticated.")
            
            print("Fetching settings...")
            settings = await client.get_settings()
            
            print(f"\nFound {len(settings)} settings modules.")
            
            print("\n--- Battery Settings Availability ---")
            battery_module = settings.get('devices:local', [])
            
            # Check specifically for the missing ones
            target_ids = ['Battery:MinSocRel', 'Battery:MinHomeConsumption']
            found_ids = [s.id for s in battery_module]
            
            for tid in target_ids:
                if tid in found_ids:
                    print(f"[OK] {tid} is available.")
                else:
                    print(f"[MISSING] {tid} is NOT available.")
            
            print("\n--- All 'devices:local' Settings ---")
            if battery_module:
                sample = battery_module[0]
                print(f"Sample Setting Object Type: {type(sample)}")
                print(f"Sample Setting Object dir: {dir(sample)}")
                print(f"Sample Setting Content: {sample}")
            
            for s in battery_module:
                 print(f"- {s.id}")

        except Exception as e:
            print(f"\nERROR: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
