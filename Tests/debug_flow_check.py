
import pytest
import sys
import voluptuous as vol
from kostal_plenticore.config_flow import DATA_SCHEMA, KostalPlenticoreConfigFlow
from kostal_plenticore.const import CONF_SERVICE_CODE
import inspect

def test_debug_config_flow():
    print(f"CONF_SERVICE_CODE: '{CONF_SERVICE_CODE}'")
    print(f"DATA_SCHEMA schema keys: {list(DATA_SCHEMA.schema.keys())}")
    
    flow = KostalPlenticoreConfigFlow()
    print(f"Has async_step_reconfigure: {hasattr(flow, 'async_step_reconfigure')}")
    print(f"KostalPlenticoreConfigFlow file: {inspect.getfile(KostalPlenticoreConfigFlow)}")
    
    # Check if 'service_code' matches schema
    try:
        DATA_SCHEMA({"host": "h", "password": "p", "service_code": "s"})
        print("Schema validation PASSED for service_code")
    except vol.Invalid as e:
        print(f"Schema validation FAILED: {e}")

