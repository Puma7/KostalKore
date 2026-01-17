"""Constants for the Kostal Plenticore Solar Inverter integration."""

from __future__ import annotations

from typing import Final

from homeassistant.const import CONF_HOST, CONF_PASSWORD

DOMAIN: Final[str] = "kostal_plenticore"
CONF_SERVICE_CODE: Final[str] = "service_code"

__all__ = [
    "CONF_HOST",
    "CONF_PASSWORD",
    "CONF_SERVICE_CODE",
    "DOMAIN",
]
