
import pytest
import voluptuous as vol
from kostal_plenticore.config_flow import DATA_SCHEMA, test_connection
from kostal_plenticore.const import CONF_SERVICE_CODE
import inspect

def test_debug_schema():
    print(f"CONF_SERVICE_CODE: '{CONF_SERVICE_CODE}'")
    print(f"DATA_SCHEMA schema keys: {list(DATA_SCHEMA.schema.keys())}")
    
    assert CONF_SERVICE_CODE in DATA_SCHEMA.schema
    assert isinstance(DATA_SCHEMA.schema[CONF_SERVICE_CODE], vol.Optional)
    
    # Check test_connection source code
    source = inspect.getsource(test_connection)
    print(f"test_connection source:\n{source}")

