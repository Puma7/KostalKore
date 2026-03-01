"""Constants for the Kostal Plenticore Solar Inverter integration."""

from __future__ import annotations

from typing import Final, TYPE_CHECKING

from homeassistant.const import CONF_HOST, CONF_PASSWORD

DOMAIN: Final[str] = "kostal_kore"
CONF_SERVICE_CODE: Final[str] = "service_code"
CONF_ACCESS_ROLE: Final[str] = "access_role"
CONF_INSTALLER_ACCESS: Final[str] = "installer_access"

# Modbus configuration keys
CONF_MODBUS_ENABLED: Final[str] = "modbus_enabled"
CONF_MODBUS_PORT: Final[str] = "modbus_port"
CONF_MODBUS_UNIT_ID: Final[str] = "modbus_unit_id"
CONF_MODBUS_ENDIANNESS: Final[str] = "modbus_endianness"
CONF_MQTT_BRIDGE_ENABLED: Final[str] = "mqtt_bridge_enabled"
CONF_MODBUS_PROXY_ENABLED: Final[str] = "modbus_proxy_enabled"
CONF_MODBUS_PROXY_PORT: Final[str] = "modbus_proxy_port"
CONF_MODBUS_PROXY_BIND: Final[str] = "modbus_proxy_bind"

DEFAULT_MODBUS_PORT: Final[int] = 1502
DEFAULT_MODBUS_UNIT_ID: Final[int] = 71
DEFAULT_MODBUS_PROXY_BIND: Final[str] = "127.0.0.1"

# Centralised import for the platform callback type.
# Older HA versions export ``AddEntitiesCallback``; newer ones renamed it to
# ``AddConfigEntryEntitiesCallback``.  Using a single location avoids the
# try/except boilerplate in every platform module.
if TYPE_CHECKING:  # pragma: no cover
    from homeassistant.helpers.entity_platform import (
        AddEntitiesCallback as AddConfigEntryEntitiesCallback,
    )
else:
    try:
        from homeassistant.helpers.entity_platform import (
            AddConfigEntryEntitiesCallback,
        )
    except ImportError:
        from homeassistant.helpers.entity_platform import (
            AddEntitiesCallback as AddConfigEntryEntitiesCallback,
        )

__all__ = [
    "AddConfigEntryEntitiesCallback",
    "CONF_ACCESS_ROLE",
    "CONF_HOST",
    "CONF_INSTALLER_ACCESS",
    "CONF_MODBUS_ENABLED",
    "CONF_MODBUS_ENDIANNESS",
    "CONF_MODBUS_PORT",
    "CONF_MODBUS_PROXY_ENABLED",
    "CONF_MODBUS_PROXY_BIND",
    "CONF_MODBUS_PROXY_PORT",
    "CONF_MODBUS_UNIT_ID",
    "CONF_MQTT_BRIDGE_ENABLED",
    "CONF_PASSWORD",
    "CONF_SERVICE_CODE",
    "DEFAULT_MODBUS_PORT",
    "DEFAULT_MODBUS_PROXY_BIND",
    "DEFAULT_MODBUS_UNIT_ID",
    "DOMAIN",
]
