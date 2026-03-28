"""Tests for battery chemistry detection and thresholds."""

from kostal_plenticore.battery_chemistry import (
    CONSERVATIVE_THRESHOLDS,
    LFP_THRESHOLDS,
    NMC_THRESHOLDS,
    detect_chemistry,
    get_battery_brand,
)


class TestChemistryDetection:

    def test_byd_is_lfp(self) -> None:
        t = detect_chemistry(0x0004)
        assert t.chemistry == "LFP"

    def test_pyontech_is_lfp(self) -> None:
        t = detect_chemistry(0x0200)
        assert t.chemistry == "LFP"

    def test_varta_is_lfp(self) -> None:
        t = detect_chemistry(0x2000)
        assert t.chemistry == "LFP"

    def test_lg_is_nmc(self) -> None:
        t = detect_chemistry(0x0040)
        assert t.chemistry == "NMC"

    def test_bmz_is_nmc(self) -> None:
        t = detect_chemistry(0x0008)
        assert t.chemistry == "NMC"

    def test_none_is_conservative(self) -> None:
        t = detect_chemistry(None)
        assert t.chemistry == "Unknown"

    def test_zero_is_no_battery(self) -> None:
        t = detect_chemistry(0)
        assert t.chemistry == "none"

    def test_unknown_code_is_conservative(self) -> None:
        t = detect_chemistry(0xFFFF)
        assert t.chemistry == "Unknown"


class TestThresholdValues:

    def test_lfp_more_heat_tolerant(self) -> None:
        assert LFP_THRESHOLDS.temp_warning_max > NMC_THRESHOLDS.temp_warning_max

    def test_lfp_more_cycles(self) -> None:
        assert LFP_THRESHOLDS.cycles_good > NMC_THRESHOLDS.cycles_good

    def test_conservative_is_safest(self) -> None:
        assert CONSERVATIVE_THRESHOLDS.temp_warning_max <= NMC_THRESHOLDS.temp_warning_max

    def test_longevity_tip_not_empty(self) -> None:
        assert len(LFP_THRESHOLDS.longevity_tip) > 50
        assert len(NMC_THRESHOLDS.longevity_tip) > 50


class TestBrandDetection:

    def test_byd(self) -> None:
        assert get_battery_brand(0x0004) == "BYD"

    def test_lg(self) -> None:
        assert get_battery_brand(0x0040) == "LG"

    def test_none(self) -> None:
        assert get_battery_brand(None) == "Unknown"
