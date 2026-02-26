"""Tests for LongevityAdvisor."""

from kostal_plenticore.battery_chemistry import LFP_THRESHOLDS, NMC_THRESHOLDS
from kostal_plenticore.health_monitor import InverterHealthMonitor
from kostal_plenticore.longevity_advisor import LongevityAdvisor


class TestBatteryTips:

    def test_no_tips_when_healthy(self) -> None:
        h = InverterHealthMonitor()
        h.update_from_modbus({"battery_temperature": 22.0, "battery_cycles": 100.0})
        h.update_battery_soh(98.0)
        a = LongevityAdvisor(h, LFP_THRESHOLDS)
        tips = [t for t in a.get_tips() if t.component == "battery"]
        assert len(tips) == 0

    def test_tip_when_avg_temp_high(self) -> None:
        h = InverterHealthMonitor()
        for _ in range(20):
            h.update_from_modbus({"battery_temperature": 35.0})
        a = LongevityAdvisor(h, LFP_THRESHOLDS)
        tips = [t for t in a.get_tips() if t.component == "battery"]
        assert any("Temperatur" in t.title or "emperatur" in t.title for t in tips)

    def test_tip_when_cycles_high(self) -> None:
        h = InverterHealthMonitor()
        h.update_from_modbus({"battery_cycles": 7000.0, "battery_temperature": 20.0})
        a = LongevityAdvisor(h, LFP_THRESHOLDS)
        tips = [t for t in a.get_tips() if t.component == "battery"]
        assert any("Zyklen" in t.title for t in tips)

    def test_nmc_more_sensitive_to_heat(self) -> None:
        h = InverterHealthMonitor()
        for _ in range(20):
            h.update_from_modbus({"battery_temperature": 28.0})
        a_nmc = LongevityAdvisor(h, NMC_THRESHOLDS)
        a_lfp = LongevityAdvisor(h, LFP_THRESHOLDS)
        nmc_tips = [t for t in a_nmc.get_tips() if t.component == "battery"]
        lfp_tips = [t for t in a_lfp.get_tips() if t.component == "battery"]
        assert len(nmc_tips) >= len(lfp_tips)


class TestInverterTips:

    def test_no_tip_cool_inverter(self) -> None:
        h = InverterHealthMonitor()
        for _ in range(20):
            h.update_from_modbus({"controller_temp": 40.0})
        a = LongevityAdvisor(h, LFP_THRESHOLDS)
        tips = [t for t in a.get_tips() if t.component == "inverter"]
        assert len(tips) == 0

    def test_tip_hot_inverter(self) -> None:
        h = InverterHealthMonitor()
        for _ in range(20):
            h.update_from_modbus({"controller_temp": 62.0})
        a = LongevityAdvisor(h, LFP_THRESHOLDS)
        tips = [t for t in a.get_tips() if t.component == "inverter"]
        assert len(tips) >= 1


class TestPVTips:

    def test_tip_dc_imbalance(self) -> None:
        h = InverterHealthMonitor()
        h.update_from_modbus({"dc1_power": 5000.0, "dc2_power": 5000.0, "dc3_power": 3000.0})
        a = LongevityAdvisor(h, LFP_THRESHOLDS)
        tips = [t for t in a.get_tips() if t.component == "pv"]
        assert any("String" in t.title or "ungleich" in t.title for t in tips)


class TestAssessments:

    def test_battery_assessment_optimal(self) -> None:
        h = InverterHealthMonitor()
        h.update_from_modbus({"battery_temperature": 22.0})
        a = LongevityAdvisor(h, LFP_THRESHOLDS)
        assert "Optimal" in a.get_battery_temp_assessment()

    def test_battery_assessment_no_data(self) -> None:
        h = InverterHealthMonitor()
        a = LongevityAdvisor(h, LFP_THRESHOLDS)
        assert "Keine" in a.get_battery_temp_assessment()

    def test_inverter_assessment_optimal(self) -> None:
        h = InverterHealthMonitor()
        h.update_from_modbus({"controller_temp": 40.0})
        a = LongevityAdvisor(h, LFP_THRESHOLDS)
        assert "Optimal" in a.get_inverter_temp_assessment()

    def test_summary_complete(self) -> None:
        h = InverterHealthMonitor()
        a = LongevityAdvisor(h, LFP_THRESHOLDS)
        s = a.get_summary()
        assert s["battery_chemistry"] == "LFP"
        assert "tip_count" in s
        assert "tips" in s
