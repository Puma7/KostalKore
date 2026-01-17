"""Platform for Kostal Plenticore switches."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import asyncio
import logging
from typing import Any, Final, cast

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddEntitiesCallback as AddConfigEntryEntitiesCallback
else:
    try:
        from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
    except ImportError:
        from homeassistant.helpers.entity_platform import AddEntitiesCallback as AddConfigEntryEntitiesCallback

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_registry import RegistryEntryDisabler
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_SERVICE_CODE
from .coordinator import PlenticoreConfigEntry, SettingDataUpdateCoordinator

from aiohttp.client_exceptions import ClientError
from pykoplenti import ApiException

# Import MODBUS exception handling from coordinator
from .coordinator import _parse_modbus_exception

_LOGGER = logging.getLogger(__name__)

# Security and performance constants
SAFETY_CHECK_DELAY_SECONDS: Final[float] = 10.0
SHADOW_MANAGEMENT_MODULE_ID: Final[str] = "devices:local"
SHADOW_MANAGEMENT_DATA_ID: Final[str] = "Generator:ShadowMgmt:Enable"
UNKNOWN_API_500_RESPONSE: Final[str] = "Unknown API response [500]"
DC_STRING_FEATURE_DATA_ID: Final[str] = "Properties:String{index}Features"
SHADOW_MANAGEMENT_SUPPORT: Final[int] = 1
SHADOW_MANAGEMENT_ADVANCED: Final[int] = 3

# Security defaults
DEFAULT_ENTITY_REGISTRY_ENABLED: Final[bool] = False
DEFAULT_INSTALLER_REQUIRED: Final[bool] = False
CONFIG_ENTITY_CATEGORY: Final[EntityCategory] = EntityCategory.CONFIG
DIAGNOSTIC_ENTITY_CATEGORY: Final[EntityCategory] = EntityCategory.DIAGNOSTIC


def _normalize_translation_key(key: str) -> str:
    """Normalize translation keys to a stable snake_case identifier."""
    normalized = key.replace(":", "_").replace(".", "_").replace(" ", "_")
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    return normalized.lower()


def _handle_api_error(err: Exception, operation: str, context: str = "") -> None:
    """
    Centralized API error handling.
    
    Args:
        err: Exception that occurred
        operation: Description of the operation being performed
        context: Additional context for better logging
    """
    if isinstance(err, ApiException):
        modbus_err = _parse_modbus_exception(err)
        _LOGGER.error("API error during %s%s: %s", operation, f" ({context})" if context else "", modbus_err.message)
    elif isinstance(err, TimeoutError):
        _LOGGER.warning("Timeout during %s%s", operation, f" ({context})" if context else "")
    elif isinstance(err, (ClientError, asyncio.TimeoutError)):
        _LOGGER.error("Network error during %s%s: %s", operation, f" ({context})" if context else "", err)
    else:
        _LOGGER.error("Unexpected error during %s%s: %s", operation, f" ({context})" if context else "", err)


def create_switch_description(
    module_id: str,
    key: str,
    name: str,
    on_value: str,
    off_value: str,
    on_label: str | None = None,
    off_label: str | None = None,
    installer_required: bool = DEFAULT_INSTALLER_REQUIRED,
    entity_registry_enabled_default: bool = DEFAULT_ENTITY_REGISTRY_ENABLED,
    entity_category: EntityCategory | None = CONFIG_ENTITY_CATEGORY,
    icon: str | None = None,
) -> PlenticoreSwitchEntityDescription:
    """
    Factory function for creating switch descriptions with security defaults.
    
    Args:
        module_id: Plenticore module identifier
        key: Setting key within the module
        name: Human-readable name for the switch
        on_value: Value when switch is on
        off_value: Value when switch is off
        on_label: Label when switch is on
        off_label: Label when switch is off
        installer_required: Whether installer access is required
        entity_registry_enabled_default: Whether entity is enabled by default
        entity_category: Entity category for the switch
        icon: Icon to display
        
    Returns:
        Configured switch entity description
    """
    return PlenticoreSwitchEntityDescription(
        module_id=module_id,
        key=key,
        name=name,
        is_on=on_value,
        on_value=on_value,
        on_label=on_label or "On",
        off_value=off_value,
        off_label=off_label or "Off",
        installer_required=installer_required,
        entity_registry_enabled_default=entity_registry_enabled_default,
        entity_category=entity_category,
        icon=icon,
    )


@dataclass(frozen=True, kw_only=True)
class PlenticoreSwitchEntityDescription(SwitchEntityDescription):
    """A class that describes plenticore switch entities."""

    module_id: str
    is_on: str
    on_value: str
    on_label: str
    off_value: str
    off_label: str
    installer_required: bool = DEFAULT_INSTALLER_REQUIRED


SWITCH_SETTINGS_DATA = [
    # Battery Strategy Switch (special case - not a simple on/off)
    create_switch_description(
        module_id="devices:local",
        key="Battery:Strategy",
        name="Battery Strategy",
        on_value="1",
        off_value="2",
        on_label="Automatic",
        off_label="Automatic economical",
        entity_registry_enabled_default=DEFAULT_ENTITY_REGISTRY_ENABLED,  # Security: Hidden by default
    ),
    # Battery Control Switches
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="Battery:ManualCharge",
        name="Battery Manual Charge",
        is_on="1",
        on_value="1",
        on_label="On",
        off_value="0",
        off_label="Off",
        installer_required=True,
    ),
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="Battery:CloseSeparator",
        name="Battery Close Separator",
        is_on="1",
        on_value="1",
        on_label="On",
        off_value="0",
        off_label="Off",
        installer_required=True,
        entity_registry_enabled_default=False,  # Security: Hidden by default
    ),
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="Battery:ComMonitor:Enable",
        name="Battery Communication Monitor",
        is_on="1",
        on_value="1",
        on_label="Enabled",
        off_value="0",
        off_label="Disabled",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,  # Security: Hidden by default
    ),
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="Battery:DynamicSoc:Enable",
        name="Battery Dynamic SoC",
        is_on="1",
        on_value="1",
        on_label="Enabled",
        off_value="0",
        off_label="Disabled",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,  # Security: Hidden by default
    ),
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="Battery:ModeHomeComsumption",
        name="Battery Mode Home Consumption",
        is_on="1",
        on_value="1",
        on_label="Enabled",
        off_value="0",
        off_label="Disabled",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,  # Security: Hidden by default
    ),
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="Battery:SmartBatteryControl:Enable",
        name="Battery Smart Control",
        is_on="1",
        on_value="1",
        on_label="Enabled",
        off_value="0",
        off_label="Disabled",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,  # Security: Hidden by default
    ),
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="Battery:TimeControl:Enable",
        name="Battery Time Control",
        is_on="1",
        on_value="1",
        on_label="Enabled",
        off_value="0",
        off_label="Disabled",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,  # Security: Hidden by default
    ),
    # Energy Management Switches
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="EnergyMgmt:AcStorage",
        name="AC Storage",
        is_on="1",
        on_value="1",
        on_label="Enabled",
        off_value="0",
        off_label="Disabled",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,  # Security: Hidden by default
    ),
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="EnergyMgmt:BatCtrl:DisabelDischarge",
        name="Battery Disable Discharge",
        is_on="1",
        on_value="1",
        on_label="Disabled",
        off_value="0",
        off_label="Enabled",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,  # Security: Hidden by default
    ),
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="EnergyMgmt:SmartControl:FallbackEnable",
        name="Smart Control Fallback",
        is_on="1",
        on_value="1",
        on_label="Enabled",
        off_value="0",
        off_label="Disabled",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,  # Security: Hidden by default
    ),
    # Active Power Control Switches
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="ActivePower:ExtCtrl:ModeGradientEnable",
        name="Active Power Gradient Mode",
        is_on="1",
        on_value="1",
        on_label="Enabled",
        off_value="0",
        off_label="Disabled",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,  # Security: Hidden by default
    ),
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="ActivePower:ExtCtrl:ModePT1Enable",
        name="Active Power PT1 Mode",
        is_on="1",
        on_value="1",
        on_label="Enabled",
        off_value="0",
        off_label="Disabled",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,  # Security: Hidden by default
    ),
    # Inverter Control Switches
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="Inverter:ActivePowerConsumLimitationEnable",
        name="Inverter Active Power Consumption Limitation",
        is_on="1",
        on_value="1",
        on_label="Enabled",
        off_value="0",
        off_label="Disabled",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,  # Security: Hidden by default
    ),
    # LVRT/HVRT Switches
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="LvrtHvrt:EnableHvrt",
        name="HVRT Enable",
        is_on="1",
        on_value="1",
        on_label="Enabled",
        off_value="0",
        off_label="Disabled",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,  # Security: Hidden by default
    ),
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="LvrtHvrt:EnableLvrt",
        name="LVRT Enable",
        is_on="1",
        on_value="1",
        on_label="Enabled",
        off_value="0",
        off_label="Disabled",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,  # Security: Hidden by default
    ),
    # Power of Frequency Switches
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="POfF:Enable",
        name="Power of Frequency Enable",
        is_on="1",
        on_value="1",
        on_label="Enabled",
        off_value="0",
        off_label="Disabled",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,  # Security: Hidden by default
    ),
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="POfU:Enable",
        name="Power of Voltage Enable",
        is_on="1",
        on_value="1",
        on_label="Enabled",
        off_value="0",
        off_label="Disabled",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,  # Security: Hidden by default
    ),
    # Digital Input Switches
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="DigitalInputs:Spd:Enable",
        name="SPD Enable",
        is_on="1",
        on_value="1",
        on_label="Enabled",
        off_value="0",
        off_label="Disabled",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,  # Security: Hidden by default
    ),
    # ESB Switches
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="ESB:SleepmodeAllowed",
        name="ESB Sleep Mode Allowed",
        is_on="1",
        on_value="1",
        on_label="Allowed",
        off_value="0",
        off_label="Not Allowed",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,  # Security: Hidden by default
    ),
    # Generator Switches
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="Generator:SwapDetection:Enable",
        name="Generator Swap Detection",
        is_on="1",
        on_value="1",
        on_label="Enabled",
        off_value="0",
        off_label="Disabled",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,  # Security: Hidden by default
    ),
    # Reactive Power Switches
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="ReactivePower:QOfUP:HoldOnVoltageReturn",
        name="Reactive Power QOfUP Hold On Voltage Return",
        is_on="1",
        on_value="1",
        on_label="Enabled",
        off_value="0",
        off_label="Disabled",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,  # Security: Hidden by default
    ),
    # Power of Frequency/Voltage Additional Switches
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="POfF:HoldPowerOnFrequencyDecrease",
        name="Power of Frequency Hold Power On Decrease",
        is_on="1",
        on_value="1",
        on_label="Enabled",
        off_value="0",
        off_label="Disabled",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,  # Security: Hidden by default
    ),
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="POfF:IncreaseEnable",
        name="Power of Frequency Increase Enable",
        is_on="1",
        on_value="1",
        on_label="Enabled",
        off_value="0",
        off_label="Disabled",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,  # Security: Hidden by default
    ),
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="POfU:HoldPowerOnVoltageDecrease",
        name="Power of Voltage Hold Power On Decrease",
        is_on="1",
        on_value="1",
        on_label="Enabled",
        off_value="0",
        off_label="Disabled",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,  # Security: Hidden by default
    ),
    # LVRT/HVRT Additional Switches
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="LvrtHvrt:AddReactivePowerBeforeFault",
        name="LVRT/HVRT Add Reactive Power Before Fault",
        is_on="1",
        on_value="1",
        on_label="Enabled",
        off_value="0",
        off_label="Disabled",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,  # Security: Hidden by default
    ),
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="LvrtHvrt:FillUpWithActiveCurrent",
        name="LVRT/HVRT Fill Up With Active Current",
        is_on="1",
        on_value="1",
        on_label="Enabled",
        off_value="0",
        off_label="Disabled",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,  # Security: Hidden by default
    ),
    # Digital Output Switches
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="DigitalOut1:ExternalCtl",
        name="Digital Out 1 External Control",
        is_on="1",
        on_value="1",
        on_label="Enabled",
        off_value="0",
        off_label="Disabled",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,  # Security: Hidden by default
    ),
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="DigitalOut2:ExternalCtl",
        name="Digital Out 2 External Control",
        is_on="1",
        on_value="1",
        on_label="Enabled",
        off_value="0",
        off_label="Disabled",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,  # Security: Hidden by default
    ),
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="DigitalOut3:ExternalCtl",
        name="Digital Out 3 External Control",
        is_on="1",
        on_value="1",
        on_label="Enabled",
        off_value="0",
        off_label="Disabled",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,  # Security: Hidden by default
    ),
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="DigitalOut4:ExternalCtl",
        name="Digital Out 4 External Control",
        is_on="1",
        on_value="1",
        on_label="Enabled",
        off_value="0",
        off_label="Disabled",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,  # Security: Hidden by default
    ),
    # Power Average Switches
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="Pave:Enable",
        name="Power Average Enable",
        is_on="1",
        on_value="1",
        on_label="Enabled",
        off_value="0",
        off_label="Disabled",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,  # Security: Hidden by default
    ),
    PlenticoreSwitchEntityDescription(
        module_id="devices:local",
        key="Pave:RampAfterPowerReductionNe",
        name="Power Average Ramp After Power Reduction",
        is_on="1",
        on_value="1",
        on_label="Enabled",
        off_value="0",
        off_label="Disabled",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,  # Security: Hidden by default
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PlenticoreConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add kostal plenticore Switch."""
    _LOGGER.info("Switch platform: Starting setup")
    plenticore = entry.runtime_data

    entities: list[Entity] = []

    # Fetch fresh settings data
    try:
        available_settings_data = await plenticore.client.get_settings()
    except (ApiException, ClientError, TimeoutError, Exception) as err:
            error_msg = str(err)
            if "Unknown API response [500]" in error_msg:
                _LOGGER.error("Inverter API returned 500 error - feature not supported on this model")
            elif isinstance(err, ApiException):
                modbus_err = _parse_modbus_exception(err)
                _LOGGER.error("Could not get settings data: %s", modbus_err.message)
            else:
                _LOGGER.error("Could not get settings data: %s", err)
            # Return early if we can't get basic settings
            return
    available_settings_data = available_settings_data or {}
    
    settings_data_update_coordinator = SettingDataUpdateCoordinator(
        hass, entry, _LOGGER, "Settings Data", timedelta(seconds=30), plenticore
    )
    for description in SWITCH_SETTINGS_DATA:
        # Check if the module even exists before trying to access its settings
        module_available = (
            description.module_id in plenticore.available_modules
            or (
                not plenticore.available_modules
                and description.module_id in available_settings_data
            )
        )
        if not module_available:
            _LOGGER.debug(
                "Skipping switch %s because module %s is not available",
                description.name,
                description.module_id,
            )
            continue

        if (
            description.module_id not in available_settings_data
            or description.key
            not in (
                setting.id for setting in available_settings_data[description.module_id]
            )
        ):
            _LOGGER.debug(
                "Skipping non existing setting data %s/%s",
                description.module_id,
                description.key,
            )
            continue
        if entry.data.get(CONF_SERVICE_CODE) is None and description.installer_required:
            _LOGGER.debug(
                "Skipping installer required setting data %s/%s",
                description.module_id,
                description.key,
            )
            continue
        entities.append(
            PlenticoreDataSwitch(
                settings_data_update_coordinator,
                description,
                entry.entry_id,
                entry.title,
                plenticore.device_info,
            )
        )

    # add shadow management switches for strings which support it
    # Wrap ENTIRE shadow management section in try-except to prevent ANY exception from crashing the platform
    # This ensures basic switches (Battery Strategy, Battery Manual Charge) are always created
    try:
        try:
            string_count_setting = await plenticore.client.get_setting_values(
                "devices:local", "Properties:StringCnt"
            )
        except (ApiException, ClientError, TimeoutError, Exception) as err:
            error_msg = str(err)
            if "Unknown API response [500]" in error_msg:
                _LOGGER.warning("Inverter API returned 500 error for string count - feature not supported")
            elif isinstance(err, ApiException):
                modbus_err = _parse_modbus_exception(err)
                _LOGGER.warning("Could not get string count: %s", modbus_err.message)
            else:
                _LOGGER.warning("Could not get string count: %s", err)
            string_count_setting = {}
        
        try:
            string_count = int(
                string_count_setting.get("devices:local", {})
                .get("Properties:StringCnt", 0)
            )
        except (ValueError, AttributeError):
            string_count = 0

        # Initialize variables for shadow management
        dc_strings = tuple(range(string_count))
        dc_string_feature_ids = tuple(
            PlenticoreShadowMgmtSwitch.DC_STRING_FEATURE_DATA_ID % dc_string
            for dc_string in dc_strings
        )
        
        # Skip shadow management if no strings
        if not dc_strings:
            _LOGGER.debug("No DC strings detected, skipping shadow management")
        else:
            dc_string_features: dict[str, dict[str, str]] = {}
            _LOGGER.debug("Attempting to get DC string features for %s", dc_string_feature_ids)
            try:
                _LOGGER.debug("Switch: Calling get_setting_values for shadow management")
                dc_string_features = cast(
                    dict[str, dict[str, str]],
                    await plenticore.client.get_setting_values(
                        PlenticoreShadowMgmtSwitch.MODULE_ID,
                        dc_string_feature_ids,
                    ),
                )
                _LOGGER.debug("Successfully got DC string features: %s", dc_string_features)
            except (ApiException, ClientError, TimeoutError, Exception) as err:
                # Handle API errors gracefully - some inverters may not support DC string features
                _handle_api_error(err, "DC string features batch query", "shadow management")
                
                error_msg = str(err)
                if UNKNOWN_API_500_RESPONSE in error_msg:
                    _LOGGER.info("DC string batch query not supported - using optimized individual queries")
                    # Use individual queries directly since batch query fails on this inverter
                    dc_string_features = {}
                    
                    _LOGGER.info("Using individual DC string feature queries (optimized for your inverter)...")
                    for dc_string, feature_id in zip(dc_strings, dc_string_feature_ids, strict=True):
                        try:
                            _LOGGER.debug("Querying string %d feature: %s", dc_string + 1, feature_id)
                            single_feature = await plenticore.client.get_setting_values(
                                PlenticoreShadowMgmtSwitch.MODULE_ID,
                                (feature_id,),
                            )
                            if single_feature:
                                dc_string_features.setdefault(PlenticoreShadowMgmtSwitch.MODULE_ID, {}).update(single_feature[PlenticoreShadowMgmtSwitch.MODULE_ID])
                                feature_value = single_feature[PlenticoreShadowMgmtSwitch.MODULE_ID].get(feature_id, '0')
                                _LOGGER.debug("String %d feature value: %s", dc_string + 1, feature_value)
                        except (ApiException, ClientError, TimeoutError, Exception) as single_err:
                            _handle_api_error(single_err, f"DC string {dc_string + 1} feature query", feature_id)
                            if UNKNOWN_API_500_RESPONSE in str(single_err):
                                _LOGGER.warning("String %d shadow management not available", dc_string + 1)
                                
                elif isinstance(err, ApiException):
                    modbus_err = _parse_modbus_exception(err)
                    _LOGGER.warning("Could not get DC string features: %s", modbus_err.message)
                    dc_string_features = {}
                else:
                    _LOGGER.warning("Could not get DC string features: %s", err)
                    dc_string_features = {}
            
            # If we still don't have features, log final state
            if not dc_string_features.get(PlenticoreShadowMgmtSwitch.MODULE_ID):
                _LOGGER.warning("Could not detect shadow management support for any DC strings")
                dc_string_features = {}

            # Create shadow management switches for strings that support it
            for dc_string, dc_string_feature_id in zip(
                dc_strings, dc_string_feature_ids, strict=True
            ):
                try:
                    dc_string_feature = int(
                        dc_string_features.get(PlenticoreShadowMgmtSwitch.MODULE_ID, {})
                        .get(dc_string_feature_id, 0)
                    )
                except (ValueError, AttributeError):
                    dc_string_feature = 0

                if dc_string_feature in (PlenticoreShadowMgmtSwitch.SHADOW_MANAGEMENT_SUPPORT, PlenticoreShadowMgmtSwitch.SHADOW_MANAGEMENT_ADVANCED):
                    feature_type = "Advanced" if dc_string_feature == PlenticoreShadowMgmtSwitch.SHADOW_MANAGEMENT_ADVANCED else "Standard"
                    _LOGGER.info("Creating %s shadow management switch for DC string %d (Feature: %d)", feature_type, dc_string + 1, dc_string_feature)
                    entities.append(
                        PlenticoreShadowMgmtSwitch(
                            settings_data_update_coordinator,
                            dc_string,
                            entry.entry_id,
                            entry.title,
                            plenticore.device_info,
                        )
                    )
                else:
                    _LOGGER.debug(
                        "DC string %d does not support shadow management (Feature: %d)",
                        dc_string + 1,
                        dc_string_feature,
                    )
    except Exception as shadow_err:
        # Catch ANY exception in shadow management setup to prevent platform crash
        # This is a catch-all to ensure basic switches are always created
        error_msg = str(shadow_err)
        if "Unknown API response [500]" in error_msg:
            _LOGGER.warning("Shadow management features not supported on this inverter model - continuing without shadow management switches")
        elif isinstance(shadow_err, ApiException):
            modbus_err = _parse_modbus_exception(shadow_err)
            _LOGGER.warning("Shadow management setup failed: %s - continuing without shadow management switches", modbus_err.message)
        else:
            _LOGGER.warning("Error setting up shadow management switches: %s - continuing without shadow management switches", shadow_err)
        # Ensure we don't crash - basic switches will still be created

    # Security: Disable existing entities BEFORE adding new ones
    # This handles the case where entities were previously enabled and need to be disabled
    # We do this BEFORE async_add_entities so we can disable existing entities immediately
    # New entities will be created with entity_registry_enabled_default=False
    disabled_count = 0
    try:
        entity_registry = er.async_get(hass)
        
        # Disable existing regular switches that should be hidden
        for description in SWITCH_SETTINGS_DATA:
            # Skip Battery:ManualCharge - it should remain enabled
            if description.key == "Battery:ManualCharge":
                continue
            
            # Only disable entities that are marked as hidden by default
            if getattr(description, "entity_registry_enabled_default", True) is not False:
                continue
            
            # Check if this setting exists in the API
            if (
                description.module_id not in available_settings_data
                or description.key
                not in (
                    setting.id for setting in available_settings_data[description.module_id]
                )
            ):
                continue
            
            # Construct the unique_id that Home Assistant uses
            unique_id = f"{entry.entry_id}_{description.module_id}_{description.key}"
            
            try:
                # Check if entity exists in registry and is currently enabled
                entity_entry = entity_registry.async_get(unique_id)
                if entity_entry and entity_entry.disabled_by is None:
                    _LOGGER.info(
                        "Security: Disabling existing switch entity %s (should be hidden by default)",
                        description.name
                    )
                    entity_registry.async_update_entity(
                        unique_id, disabled_by=RegistryEntryDisabler.INTEGRATION
                    )
                    disabled_count += 1
            except Exception as entity_err:
                # Log but don't crash - entity registry operations should be safe but handle edge cases
                _LOGGER.debug(
                    "Could not disable entity %s: %s",
                    description.name,
                    entity_err,
                )
        
        # Also disable existing shadow management switches if they exist
        # Shadow management switches use a different unique_id pattern
        for dc_string in range(3):  # Check up to 3 DC strings
            try:
                shadow_unique_id = f"{entry.entry_id}_{PlenticoreShadowMgmtSwitch.MODULE_ID}_{PlenticoreShadowMgmtSwitch.SHADOW_DATA_ID}_{dc_string}"
                shadow_entity_entry = entity_registry.async_get(shadow_unique_id)
                if shadow_entity_entry and shadow_entity_entry.disabled_by is None:
                    _LOGGER.info(
                        "Security: Disabling existing shadow management switch for DC string %d (should be hidden by default)",
                        dc_string + 1
                    )
                    entity_registry.async_update_entity(
                        shadow_unique_id, disabled_by=RegistryEntryDisabler.INTEGRATION
                    )
                    disabled_count += 1
            except Exception as shadow_err:
                # Log but don't crash - entity registry operations should be safe but handle edge cases
                _LOGGER.debug(
                    "Could not disable shadow management switch for DC string %d: %s",
                    dc_string + 1,
                    shadow_err,
                )
        
        if disabled_count > 0:
            _LOGGER.info(
                "Security: Disabled %d existing switch entities that should be hidden by default",
                disabled_count
            )
    except Exception as registry_err:
        # Catch-all to ensure entity registry errors don't crash the platform
        _LOGGER.warning(
            "Error accessing entity registry for security disabling: %s - continuing without disabling existing entities",
            registry_err,
        )
    
    # Now add entities - new ones will be created with entity_registry_enabled_default=False
    # Existing ones were just disabled above
    async_add_entities(entities)
    
    # Also schedule a delayed check as a safety net in case entities weren't in registry yet
    # This handles edge cases where entities might be added between our check and async_add_entities
    async def disable_entities_safety_check(_now: datetime) -> None:
        """Safety check to disable any entities that might have been missed."""
        try:
            entity_registry = er.async_get(hass)
            additional_disabled = 0
            
            for description in SWITCH_SETTINGS_DATA:
                if description.key == "Battery:ManualCharge":
                    continue
                if getattr(description, "entity_registry_enabled_default", True) is not False:
                    continue
                if (
                    description.module_id not in available_settings_data
                    or description.key
                    not in (
                        setting.id for setting in available_settings_data[description.module_id]
                    )
                ):
                    continue
                
                unique_id = f"{entry.entry_id}_{description.module_id}_{description.key}"
                try:
                    entity_entry = entity_registry.async_get(unique_id)
                    if entity_entry and entity_entry.disabled_by is None:
                        _LOGGER.info(
                            "Security: Disabling switch entity %s (safety check)",
                            description.name
                        )
                        entity_registry.async_update_entity(
                            unique_id, disabled_by=RegistryEntryDisabler.INTEGRATION
                        )
                        additional_disabled += 1
                except Exception:
                    pass
            
            if additional_disabled > 0:
                _LOGGER.info(
                    "Security: Disabled %d additional switch entities in safety check",
                    additional_disabled
                )
        except Exception:
            pass  # Silent fail for safety check
    
    # Schedule safety check after configured delay as a backup
    async_call_later(hass, SAFETY_CHECK_DELAY_SECONDS, disable_entities_safety_check)


class PlenticoreDataSwitch(
    CoordinatorEntity[SettingDataUpdateCoordinator], SwitchEntity
):
    """Representation of a Plenticore Switch."""

    _attr_entity_category = EntityCategory.CONFIG
    entity_description: PlenticoreSwitchEntityDescription

    def __init__(
        self,
        coordinator: SettingDataUpdateCoordinator,
        description: PlenticoreSwitchEntityDescription,
        entry_id: str,
        platform_name: str,
        device_info: DeviceInfo,
    ) -> None:
        """Create a new Switch Entity for Plenticore process data."""
        super().__init__(coordinator)
        self.entity_description = description
        self.platform_name = platform_name
        self.module_id = description.module_id
        self.data_id = description.key
        name = description.name if isinstance(description.name, str) else ""
        self._name = name
        self._is_on = description.is_on
        self._attr_has_entity_name = True
        self._attr_name = name
        self._attr_translation_key = _normalize_translation_key(description.key)
        self.on_value = description.on_value
        self.on_label = description.on_label
        self.off_value = description.off_value
        self.off_label = description.off_label
        self._attr_unique_id = f"{entry_id}_{description.module_id}_{description.key}"
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            super().available
            and self.coordinator.data is not None
            and self.module_id in self.coordinator.data
            and self.data_id in self.coordinator.data[self.module_id]
        )

    async def async_added_to_hass(self) -> None:
        """Register this entity on the Update Coordinator."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.start_fetch_data(self.module_id, self.data_id)
        )

    async def async_will_remove_from_hass(self) -> None:
        """Unregister this entity from the Update Coordinator."""
        self.coordinator.stop_fetch_data(self.module_id, self.data_id)
        await super().async_will_remove_from_hass()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn device on."""
        if await self.coordinator.async_write_data(
            self.module_id, {self.data_id: self.on_value}
        ):
            self.coordinator.name = f"{self.platform_name} {self._name} {self.on_label}"
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn device off."""
        if await self.coordinator.async_write_data(
            self.module_id, {self.data_id: self.off_value}
        ):
            self.coordinator.name = (
                f"{self.platform_name} {self._name} {self.off_label}"
            )
            await self.coordinator.async_request_refresh()

    @property
    def is_on(self) -> bool | None:
        """Return true if device is on."""
        if not self.available or self.coordinator.data is None:
            return None  # Return None during startup to show "unknown" state
        
        value = self.coordinator.data[self.module_id][self.data_id]
        is_on_state = value == self._is_on
        
        if is_on_state:
            self.coordinator.name = f"{self.platform_name} {self._name} {self.on_label}"
        else:
            self.coordinator.name = (
                f"{self.platform_name} {self._name} {self.off_label}"
            )
        return bool(is_on_state)


class PlenticoreShadowMgmtSwitch(
    CoordinatorEntity[SettingDataUpdateCoordinator], SwitchEntity
):
    """Representation of a Plenticore Switch for shadow management.

    The shadow management switch can be controlled for each DC string separately. The DC string is
    coded as bit in a single settings value, bit 0 for DC string 1, bit 1 for DC string 2, etc.

    Not all DC strings are available for shadown management, for example if one of them is used
    for a battery.
    """

    _attr_entity_category = EntityCategory.CONFIG
    entity_description: SwitchEntityDescription

    MODULE_ID: Final = "devices:local"

    SHADOW_DATA_ID: Final = "Generator:ShadowMgmt:Enable"
    """Settings id for the bit coded shadow management."""

    DC_STRING_FEATURE_DATA_ID: Final = "Properties:String%dFeatures"
    """Settings id pattern for the DC string features."""

    SHADOW_MANAGEMENT_SUPPORT: Final = 1
    """Feature value for shadow management support in the DC string features."""

    SHADOW_MANAGEMENT_ADVANCED: Final = 3
    """Feature value for advanced shadow management support."""

    def __init__(
        self,
        coordinator: SettingDataUpdateCoordinator,
        dc_string: int,
        entry_id: str,
        platform_name: str,
        device_info: DeviceInfo,
    ) -> None:
        """Create a new Switch Entity for Plenticore shadow management."""
        super().__init__(coordinator, context=(self.MODULE_ID, self.SHADOW_DATA_ID))

        self._mask: Final = 1 << dc_string

        self.entity_description = SwitchEntityDescription(
            key=f"ShadowMgmt{dc_string}",
            name=f"Shadow Management DC string {dc_string + 1}",
            entity_registry_enabled_default=False,
        )

        self.platform_name = platform_name
        self._attr_has_entity_name = True
        shadow_name = (
            self.entity_description.name
            if isinstance(self.entity_description.name, str)
            else ""
        )
        self._attr_name = shadow_name
        self._attr_unique_id = (
            f"{entry_id}_{self.MODULE_ID}_{self.SHADOW_DATA_ID}_{dc_string}"
        )
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            super().available
            and self.coordinator.data is not None
            and self.MODULE_ID in self.coordinator.data
            and self.SHADOW_DATA_ID in self.coordinator.data[self.MODULE_ID]
        )

    def _get_shadow_mgmt_value(self) -> int:
        """Return the current shadow management value for all strings as integer."""
        if not self.available or self.coordinator.data is None:
            return 0
        try:
            return int(self.coordinator.data[self.MODULE_ID][self.SHADOW_DATA_ID])
        except (ValueError, KeyError, TypeError):
            return 0

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn shadow management on."""
        shadow_mgmt_value = self._get_shadow_mgmt_value()
        shadow_mgmt_value |= self._mask

        if await self.coordinator.async_write_data(
            self.MODULE_ID, {self.SHADOW_DATA_ID: str(shadow_mgmt_value)}
        ):
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn shadow management off."""
        shadow_mgmt_value = self._get_shadow_mgmt_value()
        shadow_mgmt_value &= ~self._mask

        if await self.coordinator.async_write_data(
            self.MODULE_ID, {self.SHADOW_DATA_ID: str(shadow_mgmt_value)}
        ):
            await self.coordinator.async_request_refresh()

    @property
    def is_on(self) -> bool | None:
        """Return true if shadow management is on."""
        if not self.available or self.coordinator.data is None:
            return None  # Return None during startup to show "unknown" state
        return (self._get_shadow_mgmt_value() & self._mask) != 0
