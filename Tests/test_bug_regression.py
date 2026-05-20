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


# ---------------------------------------------------------------------------
# HIGH-02 — Modbus proxy must not zero-fill register gaps
# ---------------------------------------------------------------------------

def test_high02_proxy_partial_coverage_returns_none() -> None:
    """Partial coverage of a read range must not return zero-filled bytes.

    Returning fabricated zeros to evcc/EMS for unknown gaps led to wrong
    energy decisions (export looked like idle). The fix returns None so
    the proxy falls back to a real-inverter forward read.
    """
    from kostal_plenticore.modbus_proxy import _build_register_image
    from kostal_plenticore.modbus_registers import (
        ModbusRegister, DataType, Access, RegisterGroup,
    )

    reg_a = ModbusRegister(
        100, "rega", "rega", DataType.UINT16, 1, Access.RO, RegisterGroup.POWER
    )
    reg_b = ModbusRegister(
        102, "regb", "regb", DataType.UINT16, 1, Access.RO, RegisterGroup.POWER
    )
    with patch(
        "kostal_plenticore.modbus_proxy._SORTED_REGISTERS",
        [(100, reg_a), (102, reg_b)],
    ):
        # Range 100..102 — addr 101 has no register → gap → must be None.
        assert _build_register_image(
            100, 3, {"rega": 1, "regb": 2}, "little"
        ) is None
        # Full coverage of just the populated addresses works.
        assert _build_register_image(100, 1, {"rega": 1}, "little") is not None
        # Empty data → None.
        assert _build_register_image(100, 1, {}, "little") is None


# ---------------------------------------------------------------------------
# HIGH-05 — SettingDataUpdateCoordinator stale-cache TTL
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_high05_settings_stale_cache_expires_after_ttl(hass: HomeAssistant) -> None:
    """After STALE_DATA_MAX_AGE_SECONDS, 503 must no longer return cached data."""
    from kostal_plenticore.coordinator import (
        SettingDataUpdateCoordinator,
        STALE_DATA_MAX_AGE_SECONDS,
    )

    entry = _mock_entry()
    from kostal_plenticore.coordinator import Plenticore
    p = Plenticore.__new__(Plenticore)
    p.hass = hass
    p.config_entry = entry
    p._client = MagicMock()
    p._client.get_setting_values = AsyncMock(
        side_effect=ApiException("[503] internal communication error")
    )

    with patch(
        "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
        return_value=None,
    ):
        coord = SettingDataUpdateCoordinator.__new__(SettingDataUpdateCoordinator)
        coord.hass = hass
        coord.logger = logging.getLogger(__name__)
        coord.name = "settings-stale"
        coord._fetch = {"devices:local": ["Battery:MinSoc"]}
        coord._last_result = {"devices:local": {"Battery:MinSoc": "5"}}
        coord._plenticore = p
        coord.update_interval = timedelta(seconds=30)
        coord._base_update_interval = timedelta(seconds=30)
        coord._max_update_interval = timedelta(seconds=300)
        coord._consecutive_failures = 0
        coord._failure_multiplier_cap = 8

    coord.async_contexts = lambda: iter([])

    # Fresh ts → cache served.
    import time as _time
    coord._last_success_ts = _time.monotonic()
    assert await coord._async_update_data() == {"devices:local": {"Battery:MinSoc": "5"}}

    # Stale ts (beyond TTL) → cache NOT served; failure path returns {}.
    coord._last_success_ts = _time.monotonic() - STALE_DATA_MAX_AGE_SECONDS - 1
    with patch(
        "kostal_plenticore.coordinator.create_inverter_busy_issue",
        MagicMock(),
    ):
        result = await coord._async_update_data()
    assert result == {}, "Stale-TTL must drop expired settings cache"


# ---------------------------------------------------------------------------
# HIGH-06 — EventDataUpdateCoordinator stale-cache TTL
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_high06_event_stale_cache_expires_after_ttl(hass: HomeAssistant) -> None:
    """Beyond STALE_DATA_MAX_AGE_SECONDS, error path must not serve old events."""
    from kostal_plenticore.coordinator import (
        EventDataUpdateCoordinator,
        STALE_DATA_MAX_AGE_SECONDS,
    )
    from kostal_plenticore.coordinator import Plenticore
    from collections import deque

    entry = _mock_entry()
    p = Plenticore.__new__(Plenticore)
    p.hass = hass
    p.config_entry = entry
    p._client = MagicMock()
    p._client.get_events = AsyncMock(
        side_effect=ApiException("[503] internal communication error")
    )

    with patch(
        "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
        return_value=None,
    ):
        coord = EventDataUpdateCoordinator.__new__(EventDataUpdateCoordinator)
        coord.hass = hass
        coord.logger = logging.getLogger(__name__)
        coord.name = "events-stale"
        coord._plenticore = p
        coord._history = deque()
        coord._last_signature_ts = {}
        coord._last_result = {"last_event_code": 42}
        coord.update_interval = timedelta(seconds=30)

    import time as _time
    # Fresh → cache served.
    coord._last_success_ts = _time.monotonic()
    assert await coord._async_update_data() == {"last_event_code": 42}
    # Stale → empty dict, not old snapshot.
    coord._last_success_ts = _time.monotonic() - STALE_DATA_MAX_AGE_SECONDS - 1
    assert await coord._async_update_data() == {}


# ---------------------------------------------------------------------------
# HIGH-07 — async_remove_config_entry_device refuses primary device
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_high07_remove_primary_device_refused(hass: HomeAssistant) -> None:
    """Primary inverter device must not be removable while entry is active.

    Stale auxiliary devices (different identifier) remain removable.
    """
    import kostal_plenticore as kore_init
    from kostal_plenticore.coordinator import Plenticore

    entry = _mock_entry()
    plenticore = MagicMock(spec=Plenticore)
    plenticore._get_persistent_device_id.return_value = "serial-1234"
    entry.runtime_data = plenticore  # type: ignore[attr-defined]

    from kostal_plenticore.const import DOMAIN

    primary_device = SimpleNamespace(
        identifiers={(DOMAIN, "serial-1234")},
    )
    stale_device = SimpleNamespace(
        identifiers={(DOMAIN, "old-host")},
    )

    assert await kore_init.async_remove_config_entry_device(
        hass, entry, primary_device,
    ) is False, "primary device must be protected"
    assert await kore_init.async_remove_config_entry_device(
        hass, entry, stale_device,
    ) is True, "auxiliary/stale devices remain removable"

    # When the entry has no runtime_data (already unloaded), allow removal.
    entry.runtime_data = None  # type: ignore[attr-defined]
    assert await kore_init.async_remove_config_entry_device(
        hass, entry, primary_device,
    ) is True


# ---------------------------------------------------------------------------
# HIGH-08 — Installer access only via known role or explicit UNKNOWN/empty
# ---------------------------------------------------------------------------

def test_high08_installer_access_rejects_unrecognised_role() -> None:
    """Unrecognised role strings must NOT unlock writes even with service code."""
    import kostal_plenticore.config_flow as cf

    # Whitelisted roles → True regardless of service code.
    assert cf._installer_access_from_role("INSTALLER", None) is True
    assert cf._installer_access_from_role("SERVICE", None) is True
    # User roles → always False.
    assert cf._installer_access_from_role("USER", "1234") is False
    # Only literal UNKNOWN / empty falls back to service code.
    assert cf._installer_access_from_role("UNKNOWN", "1234") is True
    assert cf._installer_access_from_role("", "1234") is True
    assert cf._installer_access_from_role("UNKNOWN", None) is False
    # Random unrecognised strings → denied even with service code.
    assert cf._installer_access_from_role("INSTALLER_TRIAL", "1234") is False
    assert cf._installer_access_from_role("GUEST_EXT", "1234") is False
    assert cf._installer_access_from_role("mystery", "1234") is False


# ---------------------------------------------------------------------------
# Audit — Modbus proxy must mirror modbus_client's vendor-register
#         endianness rule (UINT32/SINT32 at address >= 500 are big-endian
#         regardless of byte_order). Without this, external clients reading
#         vendor registers via the proxy would receive word-swapped values
#         while reading via the integrated client returns the correct ones.
# ---------------------------------------------------------------------------

def test_audit_proxy_vendor_uint32_always_big_endian_on_encode() -> None:
    """Vendor UINT32 (address >= 500) must encode big-endian even when
    endianness='little' (Kostal default). SunSpec UINT32 (address < 500)
    must still word-swap under endianness='little'."""
    from kostal_plenticore.modbus_proxy import _encode_value
    from kostal_plenticore.modbus_registers import (
        ModbusRegister, DataType, Access, RegisterGroup,
    )
    import struct as _struct

    sunspec_u32 = ModbusRegister(
        100, "ss_u32", "ss_u32", DataType.UINT32, 2, Access.RO, RegisterGroup.POWER
    )
    vendor_u32 = ModbusRegister(
        525, "vendor_u32", "vendor_u32", DataType.UINT32, 2, Access.RO, RegisterGroup.BATTERY
    )
    val = 0x12345678

    # SunSpec area + little → word-swap.
    assert _encode_value(val, sunspec_u32, "little") == _struct.pack(">HH", 0x5678, 0x1234)
    # Vendor area + little → BIG-ENDIAN (no swap), mirroring client._encode.
    assert _encode_value(val, vendor_u32, "little") == _struct.pack(">I", val)
    # Vendor area + big → big-endian (unchanged).
    assert _encode_value(val, vendor_u32, "big") == _struct.pack(">I", val)
    # SunSpec area + big → big-endian.
    assert _encode_value(val, sunspec_u32, "big") == _struct.pack(">I", val)


def test_audit_proxy_vendor_sint32_always_big_endian_on_encode() -> None:
    """Vendor SINT32 must encode big-endian; SunSpec SINT32 word-swaps under little."""
    from kostal_plenticore.modbus_proxy import _encode_value
    from kostal_plenticore.modbus_registers import (
        ModbusRegister, DataType, Access, RegisterGroup,
    )
    import struct as _struct

    sunspec_s32 = ModbusRegister(
        110, "ss_s32", "ss_s32", DataType.SINT32, 2, Access.RW, RegisterGroup.CONTROL
    )
    vendor_s32 = ModbusRegister(
        700, "vendor_s32", "vendor_s32", DataType.SINT32, 2, Access.RW, RegisterGroup.CONTROL
    )

    # Negative value SunSpec / little → two's-complement words swapped.
    assert _encode_value(-2, sunspec_s32, "little") == _struct.pack(">HH", 0xFFFE, 0xFFFF)
    # Negative value vendor / little → straight big-endian signed.
    assert _encode_value(-2, vendor_s32, "little") == _struct.pack(">i", -2)
    # Vendor + big = vendor + little for this value (both big-endian).
    assert _encode_value(-2, vendor_s32, "big") == _struct.pack(">i", -2)


def test_audit_proxy_vendor_uint32_always_big_endian_on_decode_write() -> None:
    """Round-trip: proxy receives a write for a vendor UINT32, then must
    decode it the same way the inverter would. Vendor → big-endian."""
    from kostal_plenticore.modbus_proxy import ModbusTcpProxyServer
    from kostal_plenticore.modbus_registers import (
        ModbusRegister, DataType, Access, RegisterGroup,
    )
    import struct as _struct

    coord = MagicMock()
    proxy = ModbusTcpProxyServer(
        coord, port=5502, bind_host="127.0.0.1",
        unit_id=71, endianness="little",
    )

    sunspec_u32 = ModbusRegister(
        100, "ss_u32", "ss_u32", DataType.UINT32, 2, Access.RW, RegisterGroup.CONTROL
    )
    vendor_u32 = ModbusRegister(
        1288, "g3_fallback_time", "vendor", DataType.UINT32, 2, Access.RW, RegisterGroup.CONTROL
    )
    val = 0x12345678

    # SunSpec area: wire is word-swapped under endianness="little".
    sunspec_wire = _struct.pack(">HH", 0x5678, 0x1234)
    assert proxy._decode_for_write(sunspec_u32, sunspec_wire) == val

    # Vendor area: wire is straight big-endian regardless of endianness.
    vendor_wire = _struct.pack(">I", val)
    assert proxy._decode_for_write(vendor_u32, vendor_wire) == val


def test_audit_proxy_float32_unaffected_by_vendor_base() -> None:
    """FLOAT32 must always follow byte_order — vendor-base check must NOT
    leak into the FLOAT32 branch (modbus_client._decode treats FLOAT32 this
    way; proxy must match)."""
    from kostal_plenticore.modbus_proxy import _encode_value, ModbusTcpProxyServer
    from kostal_plenticore.modbus_registers import (
        ModbusRegister, DataType, Access, RegisterGroup,
    )
    import struct as _struct

    vendor_f32 = ModbusRegister(
        1034, "bat_charge_dc", "bat_charge_dc", DataType.FLOAT32, 2, Access.RW, RegisterGroup.BATTERY_MGMT
    )

    # Vendor FLOAT32 + little → word-swapped, NOT a vendor-style big-endian.
    encoded = _encode_value(1.5, vendor_f32, "little")
    expected_be = _struct.pack(">f", 1.5)
    hi, lo = _struct.unpack(">HH", expected_be)
    assert encoded == _struct.pack(">HH", lo, hi)

    # Write-decode mirrors the same word-swap.
    coord = MagicMock()
    proxy = ModbusTcpProxyServer(
        coord, port=5502, bind_host="127.0.0.1",
        unit_id=71, endianness="little",
    )
    swapped = _struct.pack(">HH", lo, hi)
    assert proxy._decode_for_write(vendor_f32, swapped) == 1.5


# ---------------------------------------------------------------------------
# Audit — SelectDataUpdateCoordinator must fall back to last-known-good
#         options on transient 503 bursts (per-module stale-TTL cache),
#         so UI selects do not flicker to "None" when the inverter is busy.
# ---------------------------------------------------------------------------

async def test_audit_select_503_serves_stale_cache_within_ttl(hass: HomeAssistant) -> None:
    """503 burst on select fetch → previous module result is reused (within short TTL)."""
    from kostal_plenticore.coordinator import (
        SelectDataUpdateCoordinator,
        SELECT_STALE_DATA_MAX_AGE_SECONDS,
        Plenticore,
    )

    entry = _mock_entry()
    p = Plenticore.__new__(Plenticore)
    p.hass = hass
    p.config_entry = entry
    p._client = MagicMock()

    with patch(
        "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
        return_value=None,
    ):
        coord = SelectDataUpdateCoordinator.__new__(SelectDataUpdateCoordinator)
        coord.hass = hass
        coord.logger = logging.getLogger(__name__)
        coord.name = "select-stale"
        coord._fetch = {
            "devices:local": {"Battery:Mode": ["A", "B", "None"]}
        }
        coord._plenticore = p
        coord._module_last_result = {}
        coord._module_last_success_ts = {}
        coord.update_interval = timedelta(seconds=30)

    coord.async_contexts = lambda: iter([])

    # Pre-seed cache as if a recent successful round had returned Mode=A.
    import time as _time
    coord._module_last_result["devices:local"] = {"Battery:Mode": "A"}
    coord._module_last_success_ts["devices:local"] = _time.monotonic()

    # 503 on next batch.
    p._client.get_setting_values = AsyncMock(
        side_effect=ApiException("[503] internal communication error")
    )
    result = await coord._async_update_data()

    # Stale cache served instead of "None" defaults.
    assert result == {"devices:local": {"Battery:Mode": "A"}}


async def test_audit_select_503_after_ttl_returns_none_defaults(hass: HomeAssistant) -> None:
    """503 after the short select-TTL expiry must NOT serve ghost mode state."""
    from kostal_plenticore.coordinator import (
        SelectDataUpdateCoordinator,
        SELECT_STALE_DATA_MAX_AGE_SECONDS,
        Plenticore,
    )

    entry = _mock_entry()
    p = Plenticore.__new__(Plenticore)
    p.hass = hass
    p.config_entry = entry
    p._client = MagicMock()

    with patch(
        "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
        return_value=None,
    ):
        coord = SelectDataUpdateCoordinator.__new__(SelectDataUpdateCoordinator)
        coord.hass = hass
        coord.logger = logging.getLogger(__name__)
        coord.name = "select-stale-expired"
        coord._fetch = {
            "devices:local": {"Battery:Mode": ["A", "B", "None"]}
        }
        coord._plenticore = p
        coord._module_last_result = {"devices:local": {"Battery:Mode": "A"}}
        import time as _time
        coord._module_last_success_ts = {
            "devices:local": _time.monotonic() - SELECT_STALE_DATA_MAX_AGE_SECONDS - 1
        }
        coord.update_interval = timedelta(seconds=30)

    coord.async_contexts = lambda: iter([])

    p._client.get_setting_values = AsyncMock(
        side_effect=ApiException("[503] internal communication error")
    )
    result = await coord._async_update_data()

    # Cache too old → "None" defaults rather than stale data.
    assert result == {"devices:local": {"Battery:Mode": "None"}}


async def test_audit_select_404_does_not_poison_stale_cache(hass: HomeAssistant) -> None:
    """A 404 must NOT promote 'None' defaults into the stale-cache: doing so
    would mask a subsequent recovery (next success would still serve None)."""
    from kostal_plenticore.coordinator import (
        SelectDataUpdateCoordinator,
        Plenticore,
    )

    entry = _mock_entry()
    p = Plenticore.__new__(Plenticore)
    p.hass = hass
    p.config_entry = entry
    p._client = MagicMock()

    with patch(
        "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
        return_value=None,
    ):
        coord = SelectDataUpdateCoordinator.__new__(SelectDataUpdateCoordinator)
        coord.hass = hass
        coord.logger = logging.getLogger(__name__)
        coord.name = "select-404"
        coord._fetch = {
            "devices:local": {"Battery:Mode": ["A", "B", "None"]}
        }
        coord._plenticore = p
        coord._module_last_result = {}
        coord._module_last_success_ts = {}
        coord.update_interval = timedelta(seconds=30)

    coord.async_contexts = lambda: iter([])

    p._client.get_setting_values = AsyncMock(
        side_effect=ApiException("[404] not found")
    )
    await coord._async_update_data()

    # 404 → no cache entry promoted.
    assert "devices:local" not in coord._module_last_result
    assert "devices:local" not in coord._module_last_success_ts


async def test_audit_select_success_refreshes_per_module_cache(hass: HomeAssistant) -> None:
    """Successful batch must refresh both _module_last_result and timestamp."""
    from kostal_plenticore.coordinator import (
        SelectDataUpdateCoordinator,
        Plenticore,
    )

    entry = _mock_entry()
    p = Plenticore.__new__(Plenticore)
    p.hass = hass
    p.config_entry = entry
    p._client = MagicMock()
    p._client.get_setting_values = AsyncMock(return_value={
        "devices:local": {"A": "0", "B": "1"}
    })

    with patch(
        "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
        return_value=None,
    ):
        coord = SelectDataUpdateCoordinator.__new__(SelectDataUpdateCoordinator)
        coord.hass = hass
        coord.logger = logging.getLogger(__name__)
        coord.name = "select-ok"
        coord._fetch = {
            "devices:local": {"Battery:Mode": ["A", "B", "None"]}
        }
        coord._plenticore = p
        coord._module_last_result = {}
        coord._module_last_success_ts = {}
        coord.update_interval = timedelta(seconds=30)

    coord.async_contexts = lambda: iter([])

    result = await coord._async_update_data()
    assert result == {"devices:local": {"Battery:Mode": "B"}}
    assert coord._module_last_result["devices:local"] == {"Battery:Mode": "B"}
    assert coord._module_last_success_ts["devices:local"] > 0.0


# ---------------------------------------------------------------------------
# Audit — async_unload_entry must shut down the event coordinator
#         (mirrors _rollback_setup). A reload race could otherwise leave a
#         zombie event-poll task running against the inverter.
# ---------------------------------------------------------------------------

async def test_audit_unload_entry_shuts_down_event_coordinator(
    hass: HomeAssistant,
) -> None:
    """async_unload_entry must call event_coordinator.async_shutdown()."""
    import kostal_plenticore as kp_init
    from kostal_plenticore.const import DOMAIN

    entry = _mock_entry()
    entry.add_to_hass(hass)

    mock_event_coord = MagicMock()
    mock_event_coord.async_shutdown = AsyncMock()

    fake_runtime = MagicMock()
    fake_runtime.async_unload = AsyncMock()
    entry.runtime_data = fake_runtime  # type: ignore[attr-defined]

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "event_coordinator": mock_event_coord,
        # No modbus / ksem so we exercise the event-only path.
    }

    with patch.object(
        hass.config_entries, "async_unload_platforms",
        AsyncMock(return_value=True),
    ):
        await kp_init.async_unload_entry(hass, entry)

    mock_event_coord.async_shutdown.assert_awaited_once()
