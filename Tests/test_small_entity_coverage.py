from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.kostal_kore.diagnostic_entities import (
    AreaDiagnosticSensor,
    create_diagnostic_sensors,
)
from custom_components.kostal_kore.diagnostics_engine import AreaDiagnosis, DiagStatus
from custom_components.kostal_kore.fire_safety import FireRiskLevel
from custom_components.kostal_kore.fire_safety_entities import (
    BatteryFireRiskBinarySensor,
    DCCableDangerBinarySensor,
    FireAlertCountSensor,
    FireRiskSensor,
    FireSafetyOkBinarySensor,
    IsolationDangerBinarySensor,
    create_fire_safety_binary_sensors,
    create_fire_safety_sensors,
)
from custom_components.kostal_kore.health_binary_sensor import (
    ActiveErrorsWarning,
    DCStringImbalanceWarning,
    GridFrequencyWarning,
    HighErrorRateWarning,
    create_health_binary_sensors,
)
from custom_components.kostal_kore.health_monitor import HealthLevel
from custom_components.kostal_kore.health_sensor import (
    BatteryHealthSensor,
    CommunicationReliabilitySensor,
    ControllerTempHealthSensor,
    DCStringImbalanceSensor,
    ErrorRateSensor,
    HealthLevelSensor,
    HealthScoreSensor,
    InverterStateChangeSensor,
    IsolationResistanceSensor,
    ModbusRawSensor,
    PhaseImbalanceSensor,
    create_health_sensors,
)
from custom_components.kostal_kore.longevity_advisor import LongevityTip
from custom_components.kostal_kore.longevity_entities import (
    BatteryLongevitySensor,
    InverterLongevitySensor,
    PVLongevitySensor,
    create_longevity_sensors,
)


def _tracker(
    *,
    level: HealthLevel = HealthLevel.GOOD,
    current: float | int | None = 1.0,
    trend: str = "stable",
    unit: str = "u",
    min_value: float | int | None = 0,
    max_value: float | int | None = 10,
    avg_value: float | int | None = 5,
    sample_count: int = 3,
):
    return SimpleNamespace(
        level=level,
        current=current,
        trend=trend,
        unit=unit,
        min_value=min_value,
        max_value=max_value,
        avg_value=avg_value,
        sample_count=sample_count,
    )


def _health_monitor() -> SimpleNamespace:
    battery_temperature = _tracker(current=23.4)
    controller_temperature = _tracker(current=44.4)
    isolation_tracker = _tracker(current=1234.0, unit="Ohm")
    return SimpleNamespace(
        overall_health=HealthLevel.GOOD,
        health_score=88,
        communication_reliability=97.3,
        error_rate_per_hour=1.2,
        _total_polls=10,
        _failed_polls=1,
        isolation=isolation_tracker,
        get_isolation_resistance_ohm=lambda: isolation_tracker.current,
        isolation_modbus_attributes=lambda: {
            "modbus_raw_ohm": 1234.0,
            "modbus_sentinel": False,
            "modbus_measurement_unavailable": False,
        },
        controller_temp=_tracker(current=51.2),
        battery_soh=_tracker(current=92.0),
        battery_cycles=_tracker(current=111, max_value=222, avg_value=150),
        event_count=7,
        recent_events=[
            SimpleNamespace(category="a", message="m1", level=HealthLevel.INFO),
            SimpleNamespace(category="b", message="m2", level=HealthLevel.WARNING),
        ],
        dc_string_imbalance=12.34,
        dc_string_baseline_deviation=8.76,
        dc_string_collapsed=[],
        dc_share_baseline={
            "dc1_power": {"learned_share_pct": 52.6, "recent_share_pct": 51.0},
        },
        dc1_power=_tracker(current=1000.0),
        dc2_power=_tracker(current=900.0),
        dc3_power=_tracker(current=None),
        phase_voltage_imbalance=3.21,
        phase1_voltage=_tracker(current=230.0),
        phase2_voltage=_tracker(current=229.0),
        phase3_voltage=_tracker(current=228.0),
        state_change_count=5,
        battery_temp=battery_temperature,
        grid_frequency=_tracker(level=HealthLevel.WARNING, current=51.2),
        active_error_count=_tracker(current=2),
        active_warning_count=_tracker(current=1),
        all_trackers={
            "battery_temperature": battery_temperature,
            "controller_temperature": controller_temperature,
        },
    )


def _fire_alert(category: str, risk_level: str, title: str = "t", action: str = "a") -> SimpleNamespace:
    return SimpleNamespace(category=category, risk_level=risk_level, title=title, action=action)


def _fire_monitor() -> SimpleNamespace:
    alerts = [
        _fire_alert("isolation", FireRiskLevel.HIGH, "Isolation", "Inspect"),
        _fire_alert("battery_thermal", FireRiskLevel.EMERGENCY, "Battery", "Stop"),
        _fire_alert("dc_arc_indicator", FireRiskLevel.HIGH, "Arc", "Check"),
    ]
    return SimpleNamespace(
        _total_polls=4,
        current_risk_level=FireRiskLevel.HIGH,
        active_alerts=alerts,
        alerts=alerts + [_fire_alert("other", FireRiskLevel.MONITOR)],
        alert_count=len(alerts),
    )


def _advisor() -> SimpleNamespace:
    return SimpleNamespace(
        has_battery=True,
        _health=SimpleNamespace(_total_polls=4),
        battery_chemistry="lfp",
        battery_chemistry_full="Lithium Iron Phosphate",
        get_battery_temp_assessment=lambda: "Batterie ok",
        get_inverter_temp_assessment=lambda: "Wechselrichter ok",
        get_tips=lambda: [
            LongevityTip("battery", "niedrig", "Battery Tip", "d", "a"),
            LongevityTip("inverter", "mittel", "Inverter Tip", "d", "a"),
            LongevityTip("pv", "mittel", "PV Medium", "d", "a"),
            LongevityTip("pv", "hoch", "PV High", "d", "a"),
        ],
    )


def test_health_sensor_entities_cover_status_and_value_branches() -> None:
    monitor = _health_monitor()
    device_info = {}

    score = HealthScoreSensor(monitor, "entry", device_info)
    assert score.available is True
    assert score.native_value == 88
    assert score.extra_state_attributes["overall_health"] == HealthLevel.GOOD.value

    monitor.overall_health = HealthLevel.UNKNOWN
    assert score.available is False
    assert score.native_value is None

    level_sensor = HealthLevelSensor(monitor, "entry", device_info)
    assert level_sensor.native_value == HealthLevel.UNKNOWN.value
    assert level_sensor.icon == "mdi:shield-off-outline"
    monitor.overall_health = HealthLevel.INFO
    assert level_sensor.icon == "mdi:shield-outline"
    monitor.overall_health = HealthLevel.WARNING
    assert level_sensor.icon == "mdi:shield-half-full"
    monitor.overall_health = HealthLevel.CRITICAL
    assert level_sensor.icon == "mdi:shield-alert"
    monitor.overall_health = HealthLevel.GOOD
    assert level_sensor.icon == "mdi:shield-check"

    comm = CommunicationReliabilitySensor(monitor, "entry", device_info)
    assert comm.available is True
    assert comm.native_value == pytest.approx(97.3)
    monitor._total_polls = 0
    assert comm.available is False
    assert comm.native_value is None
    monitor._total_polls = 10

    dc = DCStringImbalanceSensor(monitor, "entry", device_info)
    phase = PhaseImbalanceSensor(monitor, "entry", device_info)
    ctrl = ControllerTempHealthSensor(monitor, "entry", device_info)
    isolation = IsolationResistanceSensor(monitor, "entry", device_info)
    battery = BatteryHealthSensor(monitor, "entry", device_info)
    error_rate = ErrorRateSensor(monitor, "entry", device_info)
    state_changes = InverterStateChangeSensor(monitor, "entry", device_info)
    assert dc.native_value == pytest.approx(12.3)
    assert phase.native_value == pytest.approx(3.2)
    assert ctrl.native_value == pytest.approx(51.2)
    assert ctrl.extra_state_attributes["peak"] == 10
    assert isolation.native_value == pytest.approx(1234.0)
    assert isolation.extra_state_attributes["avg"] == 5
    assert battery.native_value == pytest.approx(92.0)
    assert battery.extra_state_attributes["cycles_total"] == 222
    assert error_rate.native_value == pytest.approx(1.2)
    assert error_rate.extra_state_attributes["total_events"] == 7
    assert state_changes.native_value == 5
    monitor.isolation.avg_value = None
    assert isolation.extra_state_attributes["avg"] is None
    monitor.dc_string_imbalance = None
    monitor.phase_voltage_imbalance = None
    assert dc.native_value is None
    assert phase.native_value is None
    assert dc.extra_state_attributes["dc1_power"] == pytest.approx(1000.0)
    assert phase.extra_state_attributes["phase1_voltage"] == pytest.approx(230.0)

    raw = ModbusRawSensor(
        monitor, "battery_temperature", "Raw", "mdi:x", "C", None, "entry", device_info, "raw"
    )
    assert raw.available is True
    assert raw.native_value == pytest.approx(23.4)
    missing = ModbusRawSensor(
        monitor, "missing", "Missing", "mdi:x", "C", None, "entry", device_info, "missing"
    )
    assert missing.available is False
    assert missing.native_value is None

    created = create_health_sensors(monitor, "entry", device_info)
    assert len(created) == 12


def test_health_binary_sensor_entities_cover_unknown_warning_and_error_branches() -> None:
    monitor = _health_monitor()
    device_info = {}

    isolation = create_health_binary_sensors(monitor, "entry", device_info)[0]
    assert isolation.is_on is False
    assert isolation.icon == "mdi:flash-off"
    assert isolation.extra_state_attributes["level"] == HealthLevel.GOOD.value

    monitor.isolation.level = HealthLevel.WARNING
    assert isolation.is_on is True
    assert isolation.icon == "mdi:flash-alert"
    monitor.isolation.level = HealthLevel.UNKNOWN
    assert isolation.is_on is None

    grid = GridFrequencyWarning(monitor, "entry", device_info)
    assert grid.is_on is True

    high_error = HighErrorRateWarning(monitor, "entry", device_info)
    assert high_error.available is True
    assert high_error.is_on is False
    assert high_error.icon == "mdi:alert-circle-check-outline"
    monitor.error_rate_per_hour = 6.2
    assert high_error.is_on is True
    assert high_error.icon == "mdi:alert-circle"
    assert high_error.extra_state_attributes["error_rate_per_hour"] == pytest.approx(6.2)
    monitor._total_polls = 0
    assert high_error.available is False
    monitor._total_polls = 10

    dc_warning = DCStringImbalanceWarning(monitor, "entry", device_info)
    # is_on keys off the LEARNED-baseline deviation, not the raw spread —
    # a permanently asymmetric (south/north) setup must not trip it.
    assert dc_warning.is_on is False
    assert dc_warning.icon == "mdi:solar-panel-large"
    monitor.dc_string_baseline_deviation = 35.0
    assert dc_warning.is_on is True
    assert dc_warning.icon == "mdi:alert"
    assert dc_warning.extra_state_attributes["baseline_deviation_pp"] == pytest.approx(35.0)
    assert dc_warning.extra_state_attributes["imbalance_percent"] == pytest.approx(12.3)
    monitor.dc_string_baseline_deviation = None
    assert dc_warning.is_on is None
    assert dc_warning.extra_state_attributes["baseline_deviation_pp"] is None
    monitor.dc_string_imbalance = None
    assert dc_warning.extra_state_attributes["imbalance_percent"] is None
    # Collapsed string trips the warning even before a baseline is learned
    # (collapsed samples never train the baseline, so bdev stays None).
    monitor.dc_string_collapsed = ["dc2_power"]
    assert dc_warning.is_on is True
    assert dc_warning.extra_state_attributes["collapsed_strings"] == ["dc2_power"]
    monitor.dc_string_collapsed = []

    active_errors = ActiveErrorsWarning(monitor, "entry", device_info)
    assert active_errors.available is True
    assert active_errors.is_on is True
    assert active_errors.icon == "mdi:alert-octagon"
    assert active_errors.extra_state_attributes["warning_count"] == 1
    monitor.active_error_count.current = 0
    assert active_errors.is_on is False
    assert active_errors.icon == "mdi:check-circle"
    monitor.active_error_count.current = None
    assert active_errors.available is False


def test_fire_safety_entities_cover_risk_icons_and_binary_states() -> None:
    monitor = _fire_monitor()
    device_info = {}

    risk = FireRiskSensor(monitor, "entry", device_info)
    assert risk.available is True
    assert risk.native_value == FireRiskLevel.HIGH
    assert risk.icon == "mdi:fire-alert"
    assert risk.extra_state_attributes["active_alert_count"] == 3
    monitor.current_risk_level = FireRiskLevel.EMERGENCY
    assert risk.icon == "mdi:fire"
    monitor.current_risk_level = FireRiskLevel.ELEVATED
    assert risk.icon == "mdi:alert"
    monitor.current_risk_level = FireRiskLevel.SAFE
    assert risk.icon == "mdi:shield-check"
    monitor._total_polls = 0
    assert risk.available is False
    assert risk.native_value is None
    monitor._total_polls = 4

    count = FireAlertCountSensor(monitor, "entry", device_info)
    assert count.available is True
    assert count.native_value == 3

    safety = FireSafetyOkBinarySensor(monitor, "entry", device_info)
    monitor.current_risk_level = FireRiskLevel.SAFE
    assert safety.is_on is False
    assert safety.icon == "mdi:shield-check"
    assert safety.extra_state_attributes["risk_level"] == FireRiskLevel.SAFE
    monitor.current_risk_level = FireRiskLevel.HIGH
    assert safety.is_on is True
    assert safety.icon == "mdi:shield-alert"
    monitor.active_alerts = []
    assert safety.is_on is True
    monitor._total_polls = 0
    assert safety.available is False
    assert safety.is_on is None
    monitor._total_polls = 4
    monitor.active_alerts = list(monitor.alerts[:3])

    isolation = IsolationDangerBinarySensor(monitor, "entry", device_info)
    battery = BatteryFireRiskBinarySensor(monitor, "entry", device_info)
    dc = DCCableDangerBinarySensor(monitor, "entry", device_info)
    assert isolation.is_on is True
    assert isolation.icon == "mdi:flash-alert"
    assert battery.is_on is True
    assert battery.icon == "mdi:battery-alert"
    assert dc.is_on is True
    assert dc.icon == "mdi:alert-octagon"

    monitor.active_alerts = []
    assert isolation.is_on is False
    assert battery.is_on is False
    assert battery.icon == "mdi:battery-check"
    assert dc.is_on is False
    assert dc.icon == "mdi:cable-data"

    assert len(create_fire_safety_sensors(monitor, "entry", device_info)) == 2
    assert len(create_fire_safety_binary_sensors(monitor, "entry", device_info)) == 4


def test_longevity_entities_cover_availability_sorting_and_empty_pv_state() -> None:
    advisor = _advisor()
    device_info = {}

    battery = BatteryLongevitySensor(advisor, "entry", device_info)
    inverter = InverterLongevitySensor(advisor, "entry", device_info)
    pv = PVLongevitySensor(advisor, "entry", device_info)

    assert battery.available is True
    assert battery.native_value == "Batterie ok"
    assert battery.extra_state_attributes["chemistry"] == "lfp"
    assert inverter.available is True
    assert inverter.native_value == "Wechselrichter ok"
    assert inverter.extra_state_attributes["tip_count"] == 1
    assert pv.available is True
    assert pv.native_value == "PV High"
    assert pv.extra_state_attributes["tip_count"] == 2

    advisor.get_tips = lambda: []
    assert pv.native_value == "Keine Auffälligkeiten"

    advisor.has_battery = False
    assert battery.available is False
    advisor.has_battery = True
    advisor._health._total_polls = 0
    assert inverter.available is False
    assert pv.available is False

    created = create_longevity_sensors(advisor, "entry", device_info)
    assert len(created) == 3
    assert isinstance(created[0], BatteryLongevitySensor)


@pytest.mark.asyncio
async def test_diagnostic_entities_cover_notification_and_dismissal_paths() -> None:
    engine = SimpleNamespace(
        diagnose_all=lambda: {
            "battery": AreaDiagnosis(
                "battery", DiagStatus.WARNUNG, "Battery warn", "detail", "action", {"x": 1}
            )
        }
    )
    sensor = AreaDiagnosticSensor(
        engine=engine,
        area="battery",
        name="Battery",
        icon_ok="mdi:ok",
        icon_warn="mdi:warn",
        icon_crit="mdi:crit",
        entry_id="entry",
        device_info={},
    )
    sensor.hass = SimpleNamespace(async_create_task=lambda coro: asyncio.create_task(coro))

    with patch(
        "custom_components.kostal_kore.notifications.notify_diagnosis",
        AsyncMock(return_value=None),
    ) as notify_diag, patch(
        "custom_components.kostal_kore.notifications.dismiss",
        AsyncMock(return_value=None),
    ) as dismiss:
        assert sensor.native_value == DiagStatus.WARNUNG
        await asyncio.sleep(0)
        notify_diag.assert_awaited_once()
        assert sensor.icon == "mdi:warn"
        assert sensor.extra_state_attributes["title"] == "Battery warn"

        engine.diagnose_all = lambda: {
            "battery": AreaDiagnosis(
                "battery", DiagStatus.KRITISCH, "Battery crit", "detail2", "action2", {}
            )
        }
        assert sensor.native_value == DiagStatus.KRITISCH
        await asyncio.sleep(0)
        assert notify_diag.await_count == 2
        assert sensor.icon == "mdi:crit"

        engine.diagnose_all = lambda: {
            "battery": AreaDiagnosis(
                "battery", DiagStatus.OK, "OK", "", "", {}
            )
        }
        assert sensor.native_value == DiagStatus.OK
        await asyncio.sleep(0)
        dismiss.assert_awaited_once()
        assert sensor.icon == "mdi:ok"

        hint_sensor = AreaDiagnosticSensor(
            engine=SimpleNamespace(
                diagnose_all=lambda: {
                    "battery": AreaDiagnosis(
                        "battery", DiagStatus.HINWEIS, "Hint", "detail3", "action3", {}
                    )
                }
            ),
            area="battery",
            name="Battery",
            icon_ok="mdi:ok",
            icon_warn="mdi:warn",
            icon_crit="mdi:crit",
            entry_id="entry",
            device_info={},
        )
        assert hint_sensor.icon == "mdi:warn"

        empty_sensor = AreaDiagnosticSensor(
            engine=SimpleNamespace(diagnose_all=lambda: {}),
            area="battery",
            name="Battery",
            icon_ok="mdi:ok",
            icon_warn="mdi:warn",
            icon_crit="mdi:crit",
            entry_id="entry",
            device_info={},
        )
        assert empty_sensor.native_value == DiagStatus.OK
        assert empty_sensor.extra_state_attributes["title"] == "Initialisierung..."

        warning_without_hass = AreaDiagnosticSensor(
            engine=SimpleNamespace(
                diagnose_all=lambda: {
                    "battery": AreaDiagnosis(
                        "battery", DiagStatus.WARNUNG, "Battery warn", "detail", "action", {}
                    )
                }
            ),
            area="battery",
            name="Battery",
            icon_ok="mdi:ok",
            icon_warn="mdi:warn",
            icon_crit="mdi:crit",
            entry_id="entry",
            device_info={},
        )
        assert warning_without_hass.native_value == DiagStatus.WARNUNG
        warning_without_hass._last_diagnosis = AreaDiagnosis(
            "battery", DiagStatus.KRITISCH, "Old", "d", "a", {}
        )
        warning_without_hass._engine = SimpleNamespace(
            diagnose_all=lambda: {
                "battery": AreaDiagnosis(
                    "battery", DiagStatus.OK, "OK", "", "", {}
                )
            }
        )
        assert warning_without_hass.native_value == DiagStatus.OK

    created = create_diagnostic_sensors(SimpleNamespace(diagnose_all=lambda: {}), "entry", {})
    assert len(created) == 5
