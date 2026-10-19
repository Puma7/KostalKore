"""Reading safety state preserves alerts without creating new log events."""
import logging

import pytest

from custom_components.kostal_kore.fire_safety import FireRiskLevel, FireSafetyMonitor
from custom_components.kostal_kore.fire_safety_entities import FireSafetyOkBinarySensor


@pytest.mark.parametrize(("data", "risk"), [
    ({"controller_temp": 90, "inverter_state": 6}, FireRiskLevel.HIGH),
    ({"isolation_resistance": 10000, "total_dc_power": 5000, "inverter_state": 6},
     FireRiskLevel.EMERGENCY),
])
def test_repeated_unsafe_state_reads_keep_monitor_warning_and_alert(data, risk, caplog):
    caplog.set_level(logging.WARNING)
    monitor = FireSafetyMonitor()
    monitor.analyze(data)
    assert monitor.current_risk_level == risk
    assert len(monitor.active_alerts) == 1
    assert len([r for r in caplog.records if "FIRE SAFETY" in r.message]) == 1
    subject = FireSafetyOkBinarySensor(monitor, "synthetic", {"identifiers": {("kostal_kore", "synthetic")}})
    caplog.clear()
    for _ in range(20):
        assert subject.is_on is True
        assert subject.icon == "mdi:shield-alert"
        assert subject.extra_state_attributes["risk_level"] == risk
    assert monitor.current_risk_level == risk
    assert len(monitor.active_alerts) == 1
    assert caplog.records == []
