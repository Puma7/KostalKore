"""Battery State-of-Health (SoH) calculator.

Derives SoH from Kostal Modbus telemetry without trusting the inverter's
own SoH register. Two derived values:

1. Current SoH (%) = current battery_work_capacity / baseline × 100, where
   baseline is the maximum work-capacity ever observed (persisted across
   restarts). The baseline self-calibrates: for a fresh battery it locks
   in early; for an aged battery the trend is tracked relative to "now".

2. 5-year projection (%): linear OLS slope of capacity_wh vs cumulative
   throughput_kwh, extrapolated using the observed annual throughput rate.
   Stays None until enough samples accumulate.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any, Final

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER: Final = logging.getLogger(__name__)

# Version history:
#   1 = throughput axis = charge + discharge (deprecated, ambiguous units).
#   2 = throughput axis = discharge only (industry-standard cycle metric).
# A version mismatch on load discards old samples but keeps the baseline,
# since baseline is just a capacity reading and not tied to the X-axis
# semantics. A mid-flight downgrade is not supported.
_STORE_VERSION: Final = 2
# 500 samples × 3h interval ≈ 62 days of full coverage; long enough that
# the OLS slope stabilises but bounded so the file stays small.
_MAX_SAMPLES: Final = 500
_SAMPLE_MIN_INTERVAL_S: Final = 3 * 3600
# Baseline can only rise (battery never gets better). Tiny upward noise is
# ignored — only readings 0.5 % above the current baseline raise it.
_BASELINE_RAISE_DELTA: Final = 0.005
# Slope needs enough points to filter measurement noise. With 3h sampling
# this means ≥ 90h of data even if all samples are clustered, but in
# practice users see a stable slope only after weeks of observation.
_MIN_SAMPLES_FOR_SLOPE: Final = 30
# Projection is only reliable once enough wall-clock time has passed to
# average over diurnal/weekly load patterns. Without this gate, the
# 5-year extrapolation would be derived from a few hours of noise and
# look authoritative while being meaningless.
_MIN_PROJECTION_AGE_S: Final = 30 * 86400.0
_SECONDS_PER_YEAR: Final = 365.25 * 86400.0
# Debounce disk writes: baseline calibration can return changed=True on
# several consecutive Modbus polls; firing async_save() each time adds
# I/O load during the already-busy reload/setup window.
_SAVE_DEBOUNCE_S: Final = 60.0
# Hard sanity ceiling on capacity readings. The Modbus outlier limit lets
# values up to 10 GWh through (default for FLOAT32 telemetry), but no
# real home battery is bigger than a few MWh. Without this guard, a one-off
# corrupted Modbus frame could lock in a baseline that no future reading
# can match, pinning the calculated SoH near zero forever.
_MAX_PLAUSIBLE_CAPACITY_WH: Final = 10_000_000.0


class _BatterySohStore(Store[dict[str, Any]]):
    """Versioned store that drops incompatible-format samples on upgrade."""

    async def _async_migrate_func(
        self,
        old_major_version: int,
        old_minor_version: int,
        old_data: dict[str, Any],
    ) -> dict[str, Any]:
        if old_major_version < 2:
            # v1 stored throughput_kwh = charge + discharge on the X-axis.
            # v2 uses discharge only. Mixing both formats in one OLS slope
            # would mis-attribute degradation — drop samples but keep the
            # baseline since it is just a capacity reading.
            _LOGGER.info(
                "BatterySohCalculator: migrating storage v%d → v%d (dropping %d "
                "old-format samples)",
                old_major_version,
                _STORE_VERSION,
                len(old_data.get("samples", []) or []),
            )
            return {
                "baseline_capacity_wh": old_data.get("baseline_capacity_wh"),
                "baseline_set_at": old_data.get("baseline_set_at"),
                "samples": [],
            }
        return old_data


class BatterySohCalculator:
    """Track battery SoH and degradation slope from Modbus telemetry."""

    def __init__(self, hass: HomeAssistant, store_key: str) -> None:
        self._hass = hass
        self._store: Store[dict[str, Any]] = _BatterySohStore(
            hass, _STORE_VERSION, store_key
        )
        self._loaded: bool = False
        self._save_task: asyncio.Task[None] | None = None
        self._baseline_capacity_wh: float | None = None
        self._baseline_set_at: float | None = None  # unix ts
        self._current_capacity_wh: float | None = None
        self._latest_cycles: float | None = None
        # Discharge throughput is the industry-standard cycle-life metric:
        # it counts only useful work output (energy delivered to the home).
        # The previous `charge + discharge` summation double-counted each
        # cycle and made degradation_per_kwh hard to compare against
        # manufacturer cycle-life specs.
        self._latest_discharge_kwh: float | None = None
        self._latest_charge_kwh: float | None = None
        # Samples: (discharge_kwh, capacity_wh, unix_ts)
        self._samples: deque[tuple[float, float, float]] = deque(
            maxlen=_MAX_SAMPLES
        )
        self._last_sample_mono: float = 0.0

    # ---------- Persistence ----------

    async def async_load(self) -> None:
        try:
            raw = await self._store.async_load()
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("BatterySohCalculator load failed: %s", e)
            raw = None
        if isinstance(raw, dict):
            self._baseline_capacity_wh = _opt_float(raw.get("baseline_capacity_wh"))
            self._baseline_set_at = _opt_float(raw.get("baseline_set_at"))
            for s in raw.get("samples", []) or []:
                if isinstance(s, (list, tuple)) and len(s) == 3:
                    parsed = (_opt_float(s[0]), _opt_float(s[1]), _opt_float(s[2]))
                    if all(p is not None for p in parsed):
                        self._samples.append(parsed)  # type: ignore[arg-type]
        self._loaded = True

    async def async_save(self) -> None:
        try:
            await self._store.async_save({
                "baseline_capacity_wh": self._baseline_capacity_wh,
                "baseline_set_at": self._baseline_set_at,
                "samples": [list(s) for s in self._samples],
            })
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("BatterySohCalculator save failed: %s", e)

    def schedule_save(self) -> None:
        """Persist state with debounce (at most one save task in flight)."""
        if self._save_task is not None and not self._save_task.done():
            return
        self._save_task = self._hass.async_create_task(self._debounced_save())

    def cancel_pending_save(self) -> None:
        """Cancel any in-flight debounced save (call on entry unload)."""
        if self._save_task is not None and not self._save_task.done():
            self._save_task.cancel()

    async def _debounced_save(self) -> None:
        try:
            await asyncio.sleep(_SAVE_DEBOUNCE_S)
            await self.async_save()
        finally:
            self._save_task = None

    # ---------- Update path ----------

    def update_from_modbus(self, data: dict[str, Any]) -> bool:
        """Ingest one Modbus snapshot. Returns True iff state changed.

        Caller schedules async_save() when this returns True.
        """
        cap = _opt_float(data.get("battery_work_capacity"))
        if cap is None or cap <= 0 or cap > _MAX_PLAUSIBLE_CAPACITY_WH:
            return False
        self._current_capacity_wh = cap

        charge_wh = _opt_float(data.get("total_dc_charge")) or 0.0
        discharge_wh = _opt_float(data.get("total_dc_discharge")) or 0.0
        # Use only discharge as the throughput axis. Each cycle increments
        # both charge and discharge by roughly equal amounts (modulo
        # round-trip losses); summing both double-counts. Discharge alone
        # is what battery vendors and academic papers use for cycle-life.
        discharge_kwh = discharge_wh / 1000.0
        self._latest_discharge_kwh = discharge_kwh
        self._latest_charge_kwh = charge_wh / 1000.0

        cycles = _opt_float(data.get("battery_cycles"))
        if cycles is not None:
            self._latest_cycles = cycles

        now_unix = time.time()
        now_mono = time.monotonic()
        changed = False

        if self._baseline_capacity_wh is None:
            self._baseline_capacity_wh = cap
            self._baseline_set_at = now_unix
            changed = True
        elif cap > self._baseline_capacity_wh * (1.0 + _BASELINE_RAISE_DELTA):
            # Reading exceeds historic max by a meaningful margin — raise
            # baseline. Happens on fresh batteries during early calibration
            # cycles, where the work-capacity reading climbs the first
            # weeks before stabilising.
            self._baseline_capacity_wh = cap
            self._baseline_set_at = now_unix
            changed = True

        # Always record on the first observation so the sample buffer has
        # an anchor point even on systems where time.monotonic() starts low.
        if (
            self._last_sample_mono == 0.0
            or now_mono - self._last_sample_mono >= _SAMPLE_MIN_INTERVAL_S
        ):
            self._samples.append((discharge_kwh, cap, now_unix))
            self._last_sample_mono = now_mono
            changed = True

        return changed

    # ---------- Derived values ----------

    @property
    def baseline_capacity_wh(self) -> float | None:
        return self._baseline_capacity_wh

    @property
    def current_capacity_wh(self) -> float | None:
        return self._current_capacity_wh

    @property
    def total_discharge_kwh(self) -> float | None:
        return self._latest_discharge_kwh

    @property
    def total_charge_kwh(self) -> float | None:
        return self._latest_charge_kwh

    @property
    def total_throughput_kwh(self) -> float | None:
        """charge + discharge — exposed as attribute for debugging/comparison.

        NOT used for slope or projection (see _latest_discharge_kwh).
        """
        if self._latest_charge_kwh is None or self._latest_discharge_kwh is None:
            return None
        return self._latest_charge_kwh + self._latest_discharge_kwh

    @property
    def cycles(self) -> float | None:
        return self._latest_cycles

    @property
    def sample_count(self) -> int:
        return len(self._samples)

    @property
    def baseline_age_days(self) -> float | None:
        if self._baseline_set_at is None:
            return None
        return (time.time() - self._baseline_set_at) / 86400.0

    @property
    def soh_pct(self) -> float | None:
        base = self._baseline_capacity_wh
        cur = self._current_capacity_wh
        if base is None or cur is None or base <= 0:
            return None
        return (cur / base) * 100.0

    @property
    def degradation_per_kwh(self) -> float | None:
        """OLS slope of capacity_wh vs discharge_kwh (Wh-lost per kWh-discharged).

        Negative slope ⇒ capacity falling with use (normal degradation).
        Returns None until at least _MIN_SAMPLES_FOR_SLOPE points exist
        AND the observation window spans at least _MIN_PROJECTION_AGE_S.
        """
        if len(self._samples) < _MIN_SAMPLES_FOR_SLOPE:
            return None
        if not self._has_min_window():
            return None
        n = len(self._samples)
        sx = sum(s[0] for s in self._samples)
        sy = sum(s[1] for s in self._samples)
        sxx = sum(s[0] * s[0] for s in self._samples)
        sxy = sum(s[0] * s[1] for s in self._samples)
        denom = n * sxx - sx * sx
        if denom == 0:
            return None
        return (n * sxy - sx * sy) / denom

    def _has_min_window(self) -> bool:
        """True when oldest and newest samples span the min projection age."""
        if len(self._samples) < 2:
            return False
        oldest_ts = self._samples[0][2]
        newest_ts = self._samples[-1][2]
        return (newest_ts - oldest_ts) >= _MIN_PROJECTION_AGE_S

    @property
    def projection_reliable(self) -> bool:
        """Surface whether the projection is based on sufficient evidence."""
        return (
            len(self._samples) >= _MIN_SAMPLES_FOR_SLOPE
            and self._has_min_window()
        )

    @property
    def annual_throughput_kwh(self) -> float | None:
        """Throughput rate inferred from oldest-to-newest sample."""
        if len(self._samples) < 2:
            return None
        first = self._samples[0]
        last = self._samples[-1]
        d_kwh = last[0] - first[0]
        d_sec = last[2] - first[2]
        if d_sec <= 0:
            return None
        return (d_kwh / d_sec) * _SECONDS_PER_YEAR

    @property
    def soh_projection_5y_pct(self) -> float | None:
        slope = self.degradation_per_kwh
        annual = self.annual_throughput_kwh
        cur = self._current_capacity_wh
        base = self._baseline_capacity_wh
        if (
            slope is None or annual is None or cur is None
            or base is None or base <= 0
        ):
            return None
        if slope >= 0:
            # Not currently degrading — best estimate is current SoH.
            return self.soh_pct
        future_throughput_kwh = annual * 5.0
        capacity_lost_wh = -slope * future_throughput_kwh
        projected_cap = max(0.0, cur - capacity_lost_wh)
        return (projected_cap / base) * 100.0


def _opt_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f
