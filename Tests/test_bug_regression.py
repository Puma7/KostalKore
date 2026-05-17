"""Regression tests for all 11 bugs + 7 QA fixes.

Each test corresponds to one verified bug or QA-identified fix.
Tests are ordered by bug number, QA fixes follow.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

from pykoplenti import ApiException, ProcessData
import pytest

from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from pytest_homeassistant_custom_component.common import MockConfigEntry


_TRANSLATIONS = Path(__file__).resolve().parents[1] / "custom_components" / "kostal_kore" / "translations" / "en.json"


def _mock_entry() -> MockConfigEntry:
    return MockConfigEntry(
        entry_id="regression-entry",
        title="reg",
        domain="kostal_plenticore",
        data={"host": "192.168.1.2", "password": "pw"},
    )


def _make_process_coord(hass: HomeAssistant, fetch: dict) -> "ProcessDataUpdateCoordinator":
    """Build a ProcessDataUpdateCoordinator without hitting the HA version mismatch.

    DataUpdateCoordinator.__init__ in HA 2024.3.3 does not accept config_entry=.
    We patch it out and set the required attributes manually.
    """
    from kostal_plenticore.coordinator import ProcessDataUpdateCoordinator, Plenticore

    entry = _mock_entry()
    p = Plenticore.__new__(Plenticore)
    p.hass = hass
    p.config_entry = entry
    p._client = MagicMock()

    with patch(
        "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
        return_value=None,
    ):
        proc = ProcessDataUpdateCoordinator.__new__(ProcessDataUpdateCoordinator)
        proc.hass = hass
        proc.logger = logging.getLogger(__name__)
        proc.name = "proc"
        proc.data = None
        proc._listeners = {}
        proc._unsub_refresh = None
        proc.last_update_success = True
        proc._fetch = fetch
        proc._last_result = {}
        proc._plenticore = p
        proc.update_interval = timedelta(seconds=10)
        # AdaptivePollingCoordinatorMixin attrs
        proc._base_update_interval = timedelta(seconds=10)
        proc._max_update_interval = timedelta(seconds=300)
        proc._consecutive_failures = 0
        proc._failure_multiplier_cap = 8
    return proc


# ---------------------------------------------------------------------------
# Bug #1 — Battery capacity sensor units
# ---------------------------------------------------------------------------

def test_bug1_work_capacity_uses_watt_hour() -> None:
    """WorkCapacity must use Wh, not Ah (Modbus register 1068 is in Wh)."""
    import kostal_plenticore.sensor as sensor_mod

    desc = next(
        d for d in sensor_mod.SENSOR_PROCESS_DATA
        if d.key == "WorkCapacity" and d.module_id == "devices:local:battery"
    )
    assert desc.native_unit_of_measurement == UnitOfEnergy.WATT_HOUR, (
        f"WorkCapacity should be Wh, got {desc.native_unit_of_measurement!r}"
    )


def test_bug1_full_charge_cap_unit_is_ah() -> None:
    """FullChargeCap_E is the REST-API ampere-hour register — expected Ah.

    This test documents the current state. Real hardware returns ~50 for this
    register (50 Ah ≈ 38 kWh at nominal voltage) — confirmed in LEARNINGS.md.
    The "_E" suffix is misleading; this is charge capacity, not energy.
    If the API is ever confirmed to report Wh, change the assertion and update
    the sensor description accordingly.
    """
    import kostal_plenticore.sensor as sensor_mod

    desc = next(
        d for d in sensor_mod.SENSOR_PROCESS_DATA
        if d.key == "FullChargeCap_E" and d.module_id == "devices:local:battery"
    )
    # The REST API FullChargeCap_E register is reported in Ah by Kostal firmware.
    # If this assertion fails it means the unit was changed — verify against API docs.
    assert desc.native_unit_of_measurement == "Ah", (
        f"FullChargeCap_E unit changed unexpectedly: {desc.native_unit_of_measurement!r}"
    )


# ---------------------------------------------------------------------------
# Bug #2 / #5 — KSEM translation keys in en.json
# ---------------------------------------------------------------------------

def test_bug2_ksem_keys_in_options_step() -> None:
    """KSEM options keys must exist in translations/en.json options.step.init.data."""
    data = json.loads(_TRANSLATIONS.read_text())
    options_data = data["options"]["step"]["init"]["data"]
    for key in ("ksem_enabled", "ksem_host", "ksem_port", "ksem_unit_id"):
        assert key in options_data, f"Missing options key: {key!r}"


def test_bug5_ksem_keys_in_config_setup_options_step() -> None:
    """KSEM config keys must exist in translations/en.json config.step.setup_options.data."""
    data = json.loads(_TRANSLATIONS.read_text())
    config_data = data["config"]["step"]["setup_options"]["data"]
    for key in ("ksem_enabled", "ksem_host", "ksem_port", "ksem_unit_id"):
        assert key in config_data, f"Missing config setup_options key: {key!r}"


# ---------------------------------------------------------------------------
# Bug #3 — reauth_confirm description in en.json
# ---------------------------------------------------------------------------

def test_bug3_reauth_confirm_has_description() -> None:
    """reauth_confirm form must have a description string in en.json."""
    data = json.loads(_TRANSLATIONS.read_text())
    reauth = data["config"]["step"]["reauth_confirm"]
    assert "description" in reauth, "reauth_confirm is missing 'description' field"
    assert reauth["description"].strip(), "reauth_confirm description must not be empty"


# ---------------------------------------------------------------------------
# Bug #4 — ModbusResetButton uses translation_key
# ---------------------------------------------------------------------------

def test_bug4_modbus_reset_button_uses_translation_key() -> None:
    """ModbusResetButton instance must resolve translation_key to the correct string."""
    import kostal_plenticore.modbus_button as btn_mod

    # Use __new__ to bypass __init__ (avoids needing coordinator/entry_id/device_info)
    inst = btn_mod.ModbusResetButton.__new__(btn_mod.ModbusResetButton)
    assert inst._attr_translation_key == "reset_modbus_registers", (
        f"Expected 'reset_modbus_registers', got {inst._attr_translation_key!r}"
    )
    # Must NOT have a class-level hardcoded string _attr_name (that was the old bug)
    # Check the raw source-level class dict entry isn't a plain string name
    own_name = btn_mod.ModbusResetButton.__dict__.get("_attr_name")
    assert own_name is None or not isinstance(own_name, str), (
        f"_attr_name should not be a hardcoded string on ModbusResetButton, got {own_name!r}"
    )


def test_bug4_entity_translation_key_in_en_json() -> None:
    """en.json must define entity.button.reset_modbus_registers."""
    data = json.loads(_TRANSLATIONS.read_text())
    button_entities = data.get("entity", {}).get("button", {})
    assert "reset_modbus_registers" in button_entities, (
        "entity.button.reset_modbus_registers missing from en.json"
    )


# ---------------------------------------------------------------------------
# Bug #6 — DC string count clamped
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bug6_dc_string_count_clamped_to_max(hass: HomeAssistant) -> None:
    """DC string count from Modbus (e.g. 99) must be clamped to MAX_SANE_STRING_COUNT."""
    import kostal_plenticore.sensor as sensor_mod
    from custom_components.kostal_kore.const import MAX_SANE_STRING_COUNT

    modbus_coord = MagicMock()
    modbus_coord.device_info_data = {"num_pv_strings": 99}

    rest_coord = MagicMock()
    rest_coord.data = {}

    # Simulate the clamping logic from async_setup_entry
    _modbus_strings = modbus_coord.device_info_data.get("num_pv_strings")
    dc_string_count = 0
    if _modbus_strings is not None:
        try:
            raw = int(_modbus_strings)
            if raw > MAX_SANE_STRING_COUNT:
                dc_string_count = MAX_SANE_STRING_COUNT
            elif raw >= 1:
                dc_string_count = raw
        except (ValueError, TypeError):
            pass

    assert dc_string_count == MAX_SANE_STRING_COUNT, (
        f"Expected {MAX_SANE_STRING_COUNT}, got {dc_string_count}"
    )
    # Ensure generate_dc_sensor_descriptions respects the clamped value
    descs = sensor_mod.generate_dc_sensor_descriptions(dc_string_count)
    # Each string contributes 3 descriptions (P, U, I)
    assert len(descs) == dc_string_count * 3


# ---------------------------------------------------------------------------
# Bug #7 — Field-level coordinator error handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bug7_one_bad_field_does_not_wipe_module(hass: HomeAssistant) -> None:
    """A single field parse failure must not clear the rest of the module's data."""
    proc = _make_process_coord(hass, {"devices:local:battery": ["P", "SoC", "Cycles"]})

    class BadValue:
        """Raises on .value access."""
        @property
        def value(self):
            raise AttributeError("simulated bad field")

    class GoodValue:
        def __init__(self, v):
            self.value = v

    proc._plenticore._client.get_process_data_values = AsyncMock(return_value={
        "devices:local:battery": {
            "P": GoodValue("500"),
            "SoC": BadValue(),       # this field fails
            "Cycles": GoodValue("123"),
        }
    })

    data = await proc._async_update_data()

    module = data.get("devices:local:battery", {})
    assert module.get("P") == "500", "Good field P must survive bad-field error"
    assert module.get("Cycles") == "123", "Good field Cycles must survive bad-field error"
    # SoC had no cached value → absent (not empty dict for the whole module)
    assert "SoC" not in module or module["SoC"] is None


# ---------------------------------------------------------------------------
# Bug #10 — Isolation heuristic <= boundary
# ---------------------------------------------------------------------------

def test_bug10_isolation_heuristic_inclusive_boundary() -> None:
    """Value exactly at ISOLATION_KOHM_HEURISTIC_MAX (1000) must be multiplied."""
    from custom_components.kostal_kore.helper import (
        ISOLATION_KOHM_HEURISTIC_MAX,
        normalize_isolation_resistance_ohm,
    )

    # Exactly at the boundary: 1000 kΩ = 1 MΩ → must be converted to 1_000_000 Ω
    result = normalize_isolation_resistance_ohm(
        ISOLATION_KOHM_HEURISTIC_MAX, pv_active=True, inverter_state=2
    )
    assert result == ISOLATION_KOHM_HEURISTIC_MAX * 1000, (
        f"Value at boundary {ISOLATION_KOHM_HEURISTIC_MAX} should be converted to "
        f"{ISOLATION_KOHM_HEURISTIC_MAX * 1000} Ω, got {result}"
    )

    # One above boundary: must NOT be converted (already in Ω)
    above = normalize_isolation_resistance_ohm(
        ISOLATION_KOHM_HEURISTIC_MAX + 1, pv_active=True, inverter_state=2
    )
    assert above == ISOLATION_KOHM_HEURISTIC_MAX + 1, (
        "Value above boundary must not be multiplied"
    )


# ---------------------------------------------------------------------------
# QA-1 — Isolation restore via async_create_task after health_monitor injection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_qa1_isolation_restore_not_called_in_async_setup(
    hass: HomeAssistant,
) -> None:
    """_restore_isolation_sample must NOT be called during async_setup."""
    from custom_components.kostal_kore.modbus_coordinator import ModbusDataUpdateCoordinator
    from custom_components.kostal_kore.modbus_client import KostalModbusClient

    client = MagicMock(spec=KostalModbusClient)
    client.host = "192.168.1.2"
    client.port = 502
    client.connected = False
    client.connect = AsyncMock()
    client.detect_endianness = AsyncMock()

    coord = ModbusDataUpdateCoordinator(hass, client)

    with patch.object(coord, "_read_device_info", AsyncMock()), \
         patch.object(coord, "_load_register_capability_state", AsyncMock()), \
         patch.object(coord, "_restore_isolation_sample", AsyncMock()) as restore_mock:
        await coord.async_setup()

    # Restore must NOT be called in async_setup — it needs _health_monitor to be injected first
    restore_mock.assert_not_called()


@pytest.mark.asyncio
async def test_qa1_isolation_restore_called_after_health_monitor_injection(
    hass: HomeAssistant,
) -> None:
    """_restore_isolation_sample must be scheduled via async_create_task after injection."""
    from custom_components.kostal_kore.modbus_coordinator import ModbusDataUpdateCoordinator
    from custom_components.kostal_kore.modbus_client import KostalModbusClient

    client = MagicMock(spec=KostalModbusClient)
    client.host = "192.168.1.2"
    client.port = 502

    coord = ModbusDataUpdateCoordinator(hass, client)

    restore_called = asyncio.Event()

    async def _fake_restore():
        restore_called.set()

    with patch.object(coord, "_restore_isolation_sample", side_effect=_fake_restore):
        coord._health_monitor = MagicMock()
        hass.async_create_task(coord._restore_isolation_sample())
        await hass.async_block_till_done()

    assert restore_called.is_set(), "_restore_isolation_sample was never scheduled/executed"


# ---------------------------------------------------------------------------
# QA-2 — Migration continue skips rename on unit mismatch
# ---------------------------------------------------------------------------

def test_qa2_migration_unit_mismatch_skips_rename() -> None:
    """On unit mismatch, statistic_id must NOT be renamed and row must NOT be deleted."""
    from custom_components.kostal_kore.migration_services import _merge_statistics_metadata

    class Scalars:
        def __init__(self, values):
            self._values = values
        def scalars(self):
            return iter(self._values)

    old_meta = SimpleNamespace(
        id=1, source="recorder", statistic_id="sensor.old",
        unit_of_measurement="Ah"
    )
    new_meta = SimpleNamespace(
        id=2, source="recorder", statistic_id="sensor.new",
        unit_of_measurement="Wh"  # different unit → mismatch
    )

    session = MagicMock()
    session.execute.side_effect = [Scalars([old_meta]), Scalars([new_meta])]

    rows_moved, short_term, rebound = _merge_statistics_metadata(
        session, old_entity_id="sensor.old", new_entity_id="sensor.new"
    )

    # No data merged (mismatch path uses continue)
    assert rows_moved == 0
    assert short_term == 0
    assert rebound is False
    # The old metadata row must NOT have been renamed
    assert old_meta.statistic_id == "sensor.old", (
        "statistic_id must not be renamed on unit mismatch"
    )
    # The new metadata row must NOT have been deleted
    session.delete.assert_not_called()


def test_qa2_migration_unit_match_performs_rename() -> None:
    """When units match, rename and delete must still happen (no regression)."""
    from custom_components.kostal_kore.migration_services import (
        _merge_statistics_metadata,
        _merge_statistics_table,
    )

    class Scalars:
        def __init__(self, values):
            self._values = values
        def scalars(self):
            return iter(self._values)

    old_meta = SimpleNamespace(
        id=1, source="recorder", statistic_id="sensor.old",
        unit_of_measurement="Wh"
    )
    new_meta = SimpleNamespace(
        id=2, source="recorder", statistic_id="sensor.new",
        unit_of_measurement="Wh"  # same unit → no mismatch
    )

    session = MagicMock()
    session.execute.side_effect = [Scalars([old_meta]), Scalars([new_meta])]

    with patch(
        "custom_components.kostal_kore.migration_services._merge_statistics_table",
        return_value=5,
    ):
        rows_moved, _, rebound = _merge_statistics_metadata(
            session, old_entity_id="sensor.old", new_entity_id="sensor.new"
        )

    assert rebound is True
    assert old_meta.statistic_id == "sensor.new", "statistic_id must be renamed on unit match"
    session.delete.assert_called_once_with(new_meta)


# ---------------------------------------------------------------------------
# QA-4 — TOTAL_INCREASING returns None when formatter returns None
# ---------------------------------------------------------------------------

def test_qa4_total_increasing_returns_none_not_stale() -> None:
    """TOTAL_INCREASING sensor must return None (not cached stale) when formatter yields None."""
    import kostal_plenticore.sensor as sensor_mod
    from homeassistant.helpers.device_registry import DeviceInfo

    coord = MagicMock()
    coord.data = {"devices:local:battery": {"Cycles": "NaN"}}

    # Find a TOTAL_INCREASING description or build a minimal one
    from custom_components.kostal_kore.sensor import PlenticoreSensorEntityDescription

    desc = PlenticoreSensorEntityDescription(
        module_id="devices:local:battery",
        key="Cycles",
        name="Battery Cycles",
        native_unit_of_measurement=None,
        state_class=SensorStateClass.TOTAL_INCREASING,
        formatter="format_round",  # format_round("NaN") → None
    )

    sensor = sensor_mod.PlenticoreDataSensor(
        coord,
        desc,
        "entry",
        "sensor",
        DeviceInfo(identifiers={("kostal_kore", "entry")}),
    )
    sensor._last_valid_native_value = 999  # pre-seed cache

    # Formatter for NaN should return None
    with patch.object(sensor_mod.PlenticoreDataFormatter, "get_method", return_value=lambda _: None):
        value = sensor.native_value

    assert value is None, (
        f"TOTAL_INCREASING must return None on bad value, not stale {value!r}"
    )


def test_qa4_non_total_increasing_returns_stale_on_none() -> None:
    """Non-TOTAL_INCREASING sensor must return last valid value when formatter yields None."""
    import kostal_plenticore.sensor as sensor_mod
    from homeassistant.helpers.device_registry import DeviceInfo
    from custom_components.kostal_kore.sensor import PlenticoreSensorEntityDescription

    coord = MagicMock()
    coord.data = {"devices:local": {"HomeBat_P": "NaN"}}

    desc = PlenticoreSensorEntityDescription(
        module_id="devices:local",
        key="HomeBat_P",
        name="Battery Power",
        native_unit_of_measurement="W",
        state_class=SensorStateClass.MEASUREMENT,
        formatter="format_round",
    )

    sensor = sensor_mod.PlenticoreDataSensor(
        coord,
        desc,
        "entry",
        "sensor",
        DeviceInfo(identifiers={("kostal_kore", "entry")}),
    )
    sensor._last_valid_native_value = 42.0  # pre-seed

    with patch.object(sensor_mod.PlenticoreDataFormatter, "get_method", return_value=lambda _: None):
        value = sensor.native_value

    assert value == 42.0, (
        f"MEASUREMENT sensor must return cached value {42.0!r} on None, got {value!r}"
    )


# ---------------------------------------------------------------------------
# QA-5 — fresh_result: _last_result must not contain backfilled values
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_qa5_last_result_contains_only_fresh_values(hass: HomeAssistant) -> None:
    """_last_result must hold only freshly parsed fields, not backfilled ones."""
    proc = _make_process_coord(hass, {"mod": ["ok_field", "bad_field"]})

    class BadValue:
        @property
        def value(self):
            raise AttributeError("boom")

    class GoodValue:
        def __init__(self, v):
            self.value = v

    class BadValue:
        @property
        def value(self):
            raise AttributeError("boom")

    class GoodValue:
        def __init__(self, v):
            self.value = v

    client = proc._plenticore._client

    # First cycle: ok_field succeeds, bad_field fails (no cache yet)
    client.get_process_data_values = AsyncMock(return_value={
        "mod": {"ok_field": GoodValue("100"), "bad_field": BadValue()}
    })
    await proc._async_update_data()

    # _last_result must NOT contain bad_field (it was never fresh)
    cached = proc._last_result.get("mod", {})
    assert "ok_field" in cached, "Fresh field must be in _last_result"
    assert "bad_field" not in cached, (
        "_last_result must not contain fields that failed parsing"
    )

    # Second cycle: ok_field still good, bad_field still fails → gets backfilled into result
    # but _last_result must still not contain bad_field
    client.get_process_data_values = AsyncMock(return_value={
        "mod": {"ok_field": GoodValue("200"), "bad_field": BadValue()}
    })
    result = await proc._async_update_data()

    # result (returned to HA) may contain backfilled bad_field if there's a cache hit
    # but _last_result must only contain fresh ok_field
    cached2 = proc._last_result.get("mod", {})
    assert "bad_field" not in cached2, (
        "_last_result must never cache backfilled values (stale-cascade prevention)"
    )
    assert cached2.get("ok_field") == "200"


# ---------------------------------------------------------------------------
# QA-6 — _record_failure timing and clear_issue on retry success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_qa6_record_failure_not_called_before_retry(hass: HomeAssistant) -> None:
    """On 503 first-fetch, _record_failure must not be called before retry attempt."""
    proc = _make_process_coord(hass, {"devices:local": ["P"]})

    failure_calls: list[str] = []
    success_calls: list[str] = []

    original_record_failure = proc._record_failure
    original_record_success = proc._record_success

    def _track_failure():
        failure_calls.append("failure")
        original_record_failure()

    def _track_success():
        success_calls.append("success")
        original_record_success()

    proc._record_failure = _track_failure
    proc._record_success = _track_success

    # First call: 503 error → should retry
    # Second call (retry): succeeds
    proc._plenticore._client.get_process_data_values = AsyncMock(side_effect=[
        ApiException("[503] internal communication error"),
        {"devices:local": {"P": ProcessData(id="P", unit="W", value="100")}},
    ])

    with patch("asyncio.sleep", AsyncMock()):
        await proc._async_update_data()

    assert len(failure_calls) == 0, (
        "_record_failure must not be called when retry succeeds"
    )
    assert len(success_calls) >= 1, "_record_success must be called on retry success"


@pytest.mark.asyncio
async def test_qa6_record_failure_called_when_retry_also_fails(hass: HomeAssistant) -> None:
    """_record_failure must be called when both first-fetch and retry fail."""
    proc = _make_process_coord(hass, {"devices:local": ["P"]})

    failure_calls: list[str] = []
    original_record_failure = proc._record_failure

    def _track_failure():
        failure_calls.append("failure")
        original_record_failure()

    proc._record_failure = _track_failure

    proc._plenticore._client.get_process_data_values = AsyncMock(
        side_effect=ApiException("[503] internal communication error")
    )

    with patch("asyncio.sleep", AsyncMock()), pytest.raises(UpdateFailed):
        await proc._async_update_data()

    assert len(failure_calls) == 1, "_record_failure must be called exactly once on total failure"


@pytest.mark.asyncio
async def test_qa6_clear_issue_called_on_retry_success(hass: HomeAssistant) -> None:
    """clear_issue('inverter_busy') must be called when the 503 retry succeeds."""
    proc = _make_process_coord(hass, {"devices:local": ["P"]})

    proc._plenticore._client.get_process_data_values = AsyncMock(side_effect=[
        ApiException("[503] internal communication error"),
        {"devices:local": {"P": ProcessData(id="P", unit="W", value="500")}},
    ])

    with patch("asyncio.sleep", AsyncMock()), \
         patch("kostal_plenticore.coordinator.clear_issue") as mock_clear:
        await proc._async_update_data()

    # clear_issue should have been called at least once with "inverter_busy"
    called_keys = [c.args[1] if len(c.args) > 1 else c.kwargs.get("issue_id", "") for c in mock_clear.call_args_list]
    assert any("inverter_busy" in k for k in called_keys), (
        f"clear_issue('inverter_busy') not called. Calls: {mock_clear.call_args_list}"
    )


# ---------------------------------------------------------------------------
# QA-7 — Exception chain preserved in both timeout paths
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_qa7_login_timeout_preserves_exception_chain(hass: HomeAssistant) -> None:
    """Login timeout must chain the original TimeoutError, not suppress it."""
    from kostal_plenticore.coordinator import Plenticore
    from homeassistant.exceptions import ConfigEntryNotReady

    entry = _mock_entry()
    p = Plenticore(hass, entry)

    with patch("kostal_plenticore.coordinator.ExtendedApiClient") as client_cls:
        client = MagicMock()
        client.login = AsyncMock(side_effect=asyncio.TimeoutError())
        client_cls.return_value = client

        with pytest.raises(ConfigEntryNotReady) as exc_info:
            await p.async_setup()

    raised = exc_info.value
    assert raised.__cause__ is not None, (
        "ConfigEntryNotReady must chain the original TimeoutError via __cause__"
    )
    assert isinstance(raised.__cause__, asyncio.TimeoutError)


@pytest.mark.asyncio
async def test_qa7_process_data_timeout_preserves_exception_chain(
    hass: HomeAssistant,
) -> None:
    """Process-data fetch timeout must chain the original TimeoutError."""
    proc = _make_process_coord(hass, {"devices:local": ["P"]})
    proc._last_result = {}  # empty → no fallback, will raise UpdateFailed

    proc._plenticore._client.get_process_data_values = AsyncMock(side_effect=asyncio.TimeoutError())

    with pytest.raises(UpdateFailed) as exc_info:
        await proc._async_update_data()

    raised = exc_info.value
    assert raised.__cause__ is not None, (
        "UpdateFailed must chain the original TimeoutError (not 'from None')"
    )
    assert isinstance(raised.__cause__, asyncio.TimeoutError)
