"""Constants for the Kostal Plenticore Solar Inverter integration."""

from __future__ import annotations

from typing import Final, TYPE_CHECKING

from homeassistant.const import CONF_HOST, CONF_PASSWORD

DOMAIN: Final[str] = "kostal_plenticore"
CONF_SERVICE_CODE: Final[str] = "service_code"

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
    "CONF_HOST",
    "CONF_PASSWORD",
    "CONF_SERVICE_CODE",
    "DOMAIN",
]
