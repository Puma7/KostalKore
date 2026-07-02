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

    def test_tip_dc_baseline_shift(self) -> None:
        """Tip fires when the learned string share pattern SHIFTS."""
        h = InverterHealthMonitor(num_bidirectional=1, dc_share_min_samples=50)
        for _ in range(200):
            h.update_from_modbus({"dc1_power": 3000.0, "dc2_power": 1000.0})
        for _ in range(40):
            h.update_from_modbus({"dc1_power": 500.0, "dc2_power": 3500.0})
        a = LongevityAdvisor(h, LFP_THRESHOLDS)
        tips = [t for t in a.get_tips() if t.component == "pv"]
        assert any("String" in t.title or "verschoben" in t.title for t in tips)

    def test_tip_for_collapsed_string(self) -> None:
        """A dead string produces a high-priority tip even before a baseline
        is learned (collapsed samples never train the baseline)."""
        h = InverterHealthMonitor(num_bidirectional=1, dc_share_min_samples=50)
        for _ in range(10):
            h.update_from_modbus({"dc1_power": 3000.0, "dc2_power": 10.0})
        a = LongevityAdvisor(h, LFP_THRESHOLDS)
        tips = [t for t in a.get_tips() if t.component == "pv"]
        assert any("ohne Leistung" in t.title for t in tips)
        assert any(t.priority == "hoch" for t in tips)

    def test_no_tip_for_stable_asymmetric_strings(self) -> None:
        """South/north setups: a PERMANENT 75/25 split is learned as normal
        and must not produce an imbalance tip (raw imbalance is ~50%)."""
        h = InverterHealthMonitor(num_bidirectional=1, dc_share_min_samples=50)
        for _ in range(200):
            h.update_from_modbus({"dc1_power": 3000.0, "dc2_power": 1000.0})
        assert h.dc_string_imbalance is not None and h.dc_string_imbalance > 20
        a = LongevityAdvisor(h, LFP_THRESHOLDS)
        tips = [t for t in a.get_tips() if t.component == "pv"]
        assert not any("String" in t.title or "verschoben" in t.title for t in tips)


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
