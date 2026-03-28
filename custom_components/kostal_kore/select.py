"""Platform for Kostal Plenticore select widgets."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any, Final

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import CoordinatorEntity, UpdateFailed

from .const import AddConfigEntryEntitiesCallback
from .const_ids import ModuleId
from .coordinator import PlenticoreConfigEntry, SelectDataUpdateCoordinator

from pykoplenti import ApiException

from aiohttp.client_exceptions import ClientError

from .helper import parse_modbus_exception

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0  # Coordinator serialises all API calls

# Performance and security constants
SELECT_UPDATE_INTERVAL_SECONDS: Final[int] = 30
SELECT_SETTINGS_FETCH_TIMEOUT_SECONDS: Final[float] = 6.0
UNKNOWN_API_500_RESPONSE: Final[str] = "Unknown API response [500]"
NONE_OPTION_VALUE: Final[str] = "None"


def _normalize_translation_key(key: str) -> str:
    """Normalize translation keys to a stable snake_case identifier."""
    normalized = key.replace(":", "_").replace(".", "_").replace(" ", "_")
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    return normalized.lower()


def _handle_select_error(err: Exception, operation: str) -> None:
    """
    Centralized error handling for select operations.
    
    Args:
        err: Exception that occurred
        operation: Description of the operation being performed
    """
    if isinstance(err, ApiException):
        modbus_err = parse_modbus_exception(err)
        _LOGGER.error("Could not get %s for select: %s", operation, modbus_err.message)
    elif isinstance(err, TimeoutError):
        _LOGGER.warning("Timeout during %s for select", operation)
    elif isinstance(err, (ClientError, asyncio.TimeoutError)):
        _LOGGER.error("Network error during %s for select: %s", operation, err)
    else:
        _LOGGER.error("Unexpected error during %s for select: %s", operation, err)


def _validate_select_options(
    description: PlenticoreSelectEntityDescription,
    available_settings_data: dict[str, Any],
) -> bool:
    """
    Validate that select options are available in settings data.
    
    Args:
        description: Select entity description
        available_settings_data: Available settings data from API
        
    Returns:
        True if all required options are available, False otherwise
    """
    if description.module_id not in available_settings_data:
        return False
    
    options = description.options or []
    needed_data_ids = {data_id for data_id in options if data_id != NONE_OPTION_VALUE}
    available_data_ids = {
        setting.id for setting in available_settings_data[description.module_id]
    }
    
    return needed_data_ids <= available_data_ids


async def _get_settings_data_safe(plenticore: Any, operation: str) -> dict[str, Any]:
    """
    Get settings data with timeout and error handling.
    
    Args:
        plenticore: Plenticore client instance
        operation: Description of the operation
        
    Returns:
        Settings data or empty dict if error occurs
    """
    try:
        getter = (
            plenticore.async_get_settings_cached
            if hasattr(plenticore, "async_get_settings_cached")
            else plenticore.client.get_settings
        )
        return await asyncio.wait_for(
            getter(),
            timeout=SELECT_SETTINGS_FETCH_TIMEOUT_SECONDS
        )
    except Exception as err:
        _handle_select_error(err, operation)
        return {}


@dataclass(frozen=True, kw_only=True)
class PlenticoreSelectEntityDescription(SelectEntityDescription):
    """A class that describes plenticore select entities."""

    module_id: str


SELECT_SETTINGS_DATA = [
    PlenticoreSelectEntityDescription(
        module_id=ModuleId.DEVICES_LOCAL,
        key="battery_charge",
        name="Battery Charging / Usage mode",
        options=[
            NONE_OPTION_VALUE,
            "Battery:SmartBatteryControl:Enable",
            "Battery:TimeControl:Enable",
        ],
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PlenticoreConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add kostal plenticore Select widget."""
    plenticore = entry.runtime_data

    # Fetch fresh settings data with timeout protection and retry
    available_settings_data = await _get_settings_data_safe(plenticore, "settings data for select")

    if not available_settings_data:
        _LOGGER.warning(
            "Initial select settings fetch failed, retrying in 1 second..."
        )
        await asyncio.sleep(1)
        available_settings_data = await _get_settings_data_safe(
            plenticore, "settings data for select (retry)"
        )
    available_settings_data = available_settings_data or {}
    
    from .const import CONF_MODBUS_ENABLED
    _modbus_active = entry.options.get(CONF_MODBUS_ENABLED, False)
    _select_interval = 90 if _modbus_active else SELECT_UPDATE_INTERVAL_SECONDS

    select_data_update_coordinator = SelectDataUpdateCoordinator(
        hass, entry, _LOGGER, "Settings Data", timedelta(seconds=_select_interval), plenticore
    )

    # Some select entities are safe to create even if settings data is temporarily
    # unavailable (e.g., inverter busy). This avoids them staying grey forever.
    FORCE_CREATE_KEYS = {"battery_charge"}

    entities = []
    for description in SELECT_SETTINGS_DATA:
        assert description.options is not None
        
        # Use centralized validation function
        if not _validate_select_options(description, available_settings_data):
            if (
                description.key in FORCE_CREATE_KEYS
                and (
                    not plenticore.available_modules
                    or description.module_id in plenticore.available_modules
                )
            ):
                _LOGGER.debug(
                    "Force creating select %s despite missing settings data",
                    description.key,
                )
            else:
                continue
        entities.append(
            PlenticoreDataSelect(
                select_data_update_coordinator,
                description,
                entry_id=entry.entry_id,
                platform_name=entry.title,
                device_info=plenticore.device_info,
            )
        )

    async_add_entities(entities)

    # Migrate legacy unique_id format (entry_id + module_id) to the new
    # stable format (entry_id + module_id + key) so existing entities
    # don't remain orphaned/grey.
    if entities:
        try:
            entity_registry = er.async_get(hass)
            entries = list(
                er.async_entries_for_config_entry(entity_registry, entry.entry_id)
            )
            entries_by_unique_id = {e.unique_id: e for e in entries if e.unique_id}

            for description in SELECT_SETTINGS_DATA:
                old_unique_id = f"{entry.entry_id}_{description.module_id}"
                new_unique_id = (
                    f"{entry.entry_id}_{description.module_id}_{description.key}"
                )
                old_entry = entries_by_unique_id.get(old_unique_id)
                new_entry = entries_by_unique_id.get(new_unique_id)

                # If both exist, prefer the old entity_id to preserve history.
                # Remove the new entry, then migrate the old entry to the new unique_id.
                if old_entry and new_entry:
                    entity_registry.async_remove(new_entry.entity_id)
                    entity_registry.async_update_entity(
                        old_entry.entity_id,
                        new_unique_id=new_unique_id,
                        disabled_by=None,
                    )
                    continue

                if old_entry:
                    entity_registry.async_update_entity(
                        old_entry.entity_id,
                        new_unique_id=new_unique_id,
                        disabled_by=None,
                    )
        except Exception as err:
            _LOGGER.debug("Select entity registry migration failed: %s", err)


class PlenticoreDataSelect(
    CoordinatorEntity[SelectDataUpdateCoordinator], SelectEntity
):
    """Representation of a Plenticore Select."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: SelectDataUpdateCoordinator,
        description: PlenticoreSelectEntityDescription,
        entry_id: str,
        platform_name: str,
        device_info: DeviceInfo,
    ) -> None:
        """Create a new Select Entity for Plenticore process data."""
        super().__init__(coordinator)
        self.entity_description = description
        self.entry_id = entry_id
        self.platform_name = platform_name
        self.module_id = description.module_id
        self.data_id = description.key
        self._attr_device_info = device_info
        self._attr_unique_id = f"{entry_id}_{description.module_id}_{description.key}"
        self._attr_has_entity_name = True
        name = description.name if isinstance(description.name, str) else ""
        self._attr_name = name
        self._attr_translation_key = _normalize_translation_key(description.key)

    @property
    def available(self) -> bool:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return if entity is available."""
        return (
            super().available
            and self.coordinator.data is not None
            and self.module_id in self.coordinator.data
        )

    async def async_added_to_hass(self) -> None:
        """Register this entity on the Update Coordinator."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.start_fetch_data(
                self.module_id, self.data_id, self.options
            )
        )
        # Ensure we fetch the initial state once the entity is registered.
        try:
            await self.coordinator.async_request_refresh()
        except (
            ApiException,
            ClientError,
            TimeoutError,
            asyncio.TimeoutError,
            UpdateFailed,
            RuntimeError,
        ) as err:
            _LOGGER.debug("Initial select refresh failed: %s", err)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister this entity from the Update Coordinator."""
        self.coordinator.stop_fetch_data(self.module_id, self.data_id, self.options)
        await super().async_will_remove_from_hass()

    async def async_select_option(self, option: str) -> None:
        """Update the current selected option."""
        # CHANGELOG (Codex, 2026-02-05):
        # Fix review finding #2. Validate service-call input to ensure only
        # declared options can be written (defense-in-depth).
        if option not in self.options:
            raise ValueError(f"Invalid select option for {self.entity_id}: {option}")

        for all_option in self.options:
            if all_option != "None":
                await self.coordinator.async_write_data(
                    self.module_id, {all_option: "0"}
                )
        if option != "None":
            await self.coordinator.async_write_data(self.module_id, {option: "1"})
        self.async_write_ha_state()

    @property
    def current_option(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the selected entity option to represent the entity state."""
        if self.available:
            return self.coordinator.data[self.module_id].get(
                self.data_id, NONE_OPTION_VALUE
            )

        return None
