# KOSTAL KORE Implementation Backlog (Post-Analysis)

Status: implemented (2026-03-02)  
Scope: features inspired by `kostal-ksem`, `kostal-modbusquery`, `kostal-RESTAPI`, `pykoplenti`, reworked for KORE architecture

## Guiding principles (non-negotiable)

1. **Safety over feature count**  
   No write path is added without capability probing, explicit installer gating, and bounded value validation.
2. **Capability-driven behavior**  
   Detect supported modules/settings/registers dynamically. No blind assumptions by firmware generation.
3. **Fail-soft reads, fail-safe writes**  
   Read paths may degrade to partial data; write paths must block on uncertainty.
4. **Performance by design**  
   Use batched requests, adaptive polling, and deduped coordinator workloads.
5. **Privacy and diagnostics hygiene**  
   Redact secrets and high-risk identifiers by default; expose detailed diagnostics only when safe.

---

## Milestone overview

- **M1 (P0):** Event intelligence + Modbus data quality hardening
- **M2 (P1):** Advanced installer controls with strong safeguards
- **M3 (P1/P2):** KSEM optional integration and deeper observability

### Implementation outcome snapshot (2026-03-02)

- ✅ **Epic A delivered**: Event coordinator, bounded event history, dedup/cooldown, event snapshot sensors, diagnostics event payload.
- ✅ **Epic B delivered**: Sentinel/outlier handling (incl. register 575 behavior), expanded powermeter/battery register coverage, register capability persistence.
- ✅ **Epic C delivered with safety tightening**: allowlist, arming switch, cross-field validation, write verification.
- ✅ **Epic D delivered (optional)**: KSEM coordinator/options, diagnostics sensors, source precedence + confidence metadata.
- ✅ **Epic E delivered**: adaptive polling backoff/jitter and capability caching to reduce duplicate probes.
- ✅ **Epic F delivered**: dangerous-write allowlist and policy enforcement for high-impact controls.

### Post-implementation learning (critical policy update)

- **Battery charge/discharge setpoint control is now intentionally Modbus-only.**
  - Reason: REST behavior for these controls is not reliably deterministic across supported devices/firmware.
  - Result: REST write paths for those IDs are blocked; equivalent Modbus controls remain available.
- **Auto-discovery can work but is best-effort only.**
  - Works in reachable local networks.
  - Fails in segmented/firewalled topologies and must fallback to manual host entry.

---

## Epic A — Event intelligence (REST event stream, not just counters)

Priority: **P0**  
Why: current integration exposes event counters; users need actionable fault context.

### A1. Add event snapshot entities from `get_events()`
- Add a small set of derived entities:
  - `last_event_code`
  - `last_event_category`
  - `last_event_age`
  - `active_error_events_count` (independent from existing raw counters)
- Keep payload compact; no unbounded attribute lists in state.

### A2. Add diagnostics-only event history panel payload
- Store recent normalized events in coordinator memory ring buffer (bounded, e.g. last 50).
- Expose full detail only in diagnostics payload (not normal state attributes).

### A3. Noise suppression and dedup strategy
- Dedup by `(code, category, is_active)` with debounce window.
- Suppress flapping notification storms by cooldown (e.g. same signature within 5 min).

### Acceptance criteria
- No increase in entity update latency > 10% on default polling.
- No event payload larger than HA-friendly thresholds in entity state.
- Integration remains fully functional when event endpoint is unavailable.

### Test plan
- Unit tests: mapping/parsing/dedup/cooldown edge cases.
- Integration tests: event endpoint success, timeout, auth error, malformed payload.
- Regression tests: existing counters still function and stay stable.

---

## Epic B — Modbus data quality hardening + coverage upgrade

Priority: **P0**  
Why: field scripts reveal practical register quirks and useful missing telemetry.

### B1. Sentinel/outlier guardrails for known problematic registers
- Implement per-register validation hooks in Modbus coordinator/client layer.
- Initial rule set:
  - register 575 (`inverter_gen_power`) sentinel `32767` treated as invalid sample.
  - add configurable outlier clipping policy (drop sample, keep previous, mark degraded).

### B2. Expand register coverage for powermeter phase telemetry
- Add useful missing addresses from historical field usage:
  - `222..250` family (phase current/active/reactive/apparent/voltage variants).
- Surface as diagnostics-first sensors (disabled by default), then graduate based on reliability.

### B3. Add missing battery metadata registers (diagnostic category)
- Evaluate and add safe read-only metadata addresses where model support is broad:
  - candidates: `517, 525, 527, 529, 586` (+ optional `515`, `580`).
- Keep model/firmware gating to avoid noisy unavailable logs.

### B4. Register capability learning cache
- Persist “available/unavailable” register knowledge per device+firmware signature.
- Auto-expire or invalidate on firmware version change.
- Reduces repeated probe noise and startup cost.

### Acceptance criteria
- Unknown/unavailable register logs reduced by at least 60% after first 3 cycles.
- No increase in Modbus write failure rate.
- Added sensors do not break existing energy dashboard compatibility.

### Test plan
- Unit tests: per-register validation pipeline and fallback semantics.
- Integration tests: unavailable-register suppression lifecycle + firmware-change invalidation.
- Performance test: polling cycle time before/after coverage expansion.

---

## Epic C — Advanced installer controls (REST settings), secure by default

Priority: **P1**  
Why: advanced users need digital output + backup mode controls, but risk profile is high.

### C1. Add guarded support for selected missing settings
- Candidate settings:
  - `Battery:BackupMode:Enable`
  - `DigitalOutputs:Customer:ConfigurationFlags`
  - `DigitalOutputs:Customer:DelayTime`
  - `DigitalOutputs:Customer:PowerMode:OnPowerThreshold`
  - `DigitalOutputs:Customer:PowerMode:OffPowerThreshold`
  - `DigitalOutputs:Customer:TimeMode:PowerThreshold`
  - `DigitalOutputs:Customer:TimeMode:StableTime`
  - `DigitalOutputs:Customer:TimeMode:RunTime`
  - `DigitalOutputs:Customer:TimeMode:MaxNoOfSwitchingCyclesPerDay`
- Exclude `Battery:Type` from normal UI until strong compatibility evidence exists.

### C2. “Two-step arm” write protection for high-impact controls
- Introduce temporary arming switch (expires automatically, e.g. 2 minutes).
- Writes blocked unless:
  1) installer role confirmed,
  2) arm state active,
  3) inverter mode/capability checks pass.

### C3. Policy engine for bounds and cross-field validation
- Validate not only ranges, but relationships:
  - Off threshold must be lower than On threshold
  - RunTime/StableTime within safe domain
  - Flag combinations allowed by known profile
- Return actionable HA repair messages for violations.

### C4. Transactional write strategy
- Read-before-write baseline.
- Write single logical change set.
- Read-after-write verify.
- On mismatch: notify and mark as partial failure; never silently accept.

### Acceptance criteria
- No advanced entity enabled by default for non-installer context.
- Invalid control combinations are rejected pre-write with clear reason.
- Every successful write has read-after-write confirmation.

### Test plan
- Unit tests: policy engine, arm-expiry logic, relationship validators.
- Integration tests: permission denied, 404 unsupported setting, 500/503 busy.
- Security tests: ensure advanced writes are impossible without arm + installer access.

---

## Epic D — Optional KSEM integration (separate endpoint, separate failure domain)

Priority: **P1/P2**  
Why: KSEM can provide stronger grid metrics in some topologies.

### D1. Add optional KSEM config block (disabled by default)
- New options:
  - host, port, unit-id, enable flag
  - independent polling interval
- Keep separate coordinator and error domain from inverter polling.

### D2. Normalize KSEM metrics into KORE computed layer
- Map import/export active power and per-phase metrics to `_calc_` namespace.
- Use deterministic source precedence:
  1) KSEM if healthy
  2) inverter powermeter
  3) REST statistics fallback

### D3. Conflict-aware reconciliation
- If sources diverge beyond threshold, set diagnostics warning and expose source confidence.
- Do not overwrite core energy entities silently.

### Acceptance criteria
- Inverter functionality unchanged when KSEM is disabled or offline.
- KSEM failures never block REST/Modbus inverter entities.
- Source-of-truth metadata visible in diagnostics.

### Test plan
- Integration tests with simulated partial outages per source.
- Data consistency tests for source precedence and reconciliation behavior.

---

## Epic E — Reliability and performance upgrades

Priority: **P1**  
Why: new feature surface must not regress cycle time or stability.

### E1. Adaptive polling controller
- Increase/decrease update interval based on:
  - repeated 503/busy responses
  - communication reliability
  - active write operations
- Add jitter to avoid burst alignment.

### E2. Centralized capability map service
- Single in-memory capability registry shared by sensor/number/switch/select setup.
- Eliminates duplicate probe requests during startup.

### E3. Structured error taxonomy propagation
- Standardize API/Modbus error classes into machine-readable categories.
- Use categories for:
  - repair issue generation
  - backoff policy
  - diagnostics summary stats.

### Acceptance criteria
- Startup requests reduced measurably (target: -25% duplicate probes).
- Poll loop remains stable under simulated busy periods.

### Test plan
- Metrics-based tests around coordinator call counts.
- Chaos-style tests with injected transient/permanent error mixes.

---

## Epic F — Security hardening increment

Priority: **P1**  
Why: advanced control features increase blast radius.

### F1. Secret and identifier redaction audit
- Extend diagnostics redaction set to include any newly added identifiers.
- Ensure no event payload leaks auth/session data.

### F2. Dangerous-write allowlist
- Explicit allowlist for writable IDs/registers.
- Deny unknown write targets even if discovered dynamically.

### F3. Operator safety UX
- Add concise warning text in entity descriptions for high-impact settings.
- Require confirmation flow for irreversible/high-risk actions.

### Acceptance criteria
- Security tests confirm no write outside allowlist is possible.
- Diagnostics snapshots pass redaction checks for all new fields.

---

## Delivery order (recommended)

1. **A1-A3 + B1** (highest value, low risk)  
2. **B2-B4** (quality and observability, moderate risk)  
3. **C1-C4** (high utility, high safety requirement)  
4. **E1-E3 + F1-F3** (hardening sweep)  
5. **D1-D3** (optional KSEM feature track)

---

## Definition of done (global)

- Mypy clean (`python -m mypy custom_components/kostal_kore/`)
- Tests pass in `Tests/` except known pre-existing lingering-timer failures
- New features include:
  - docs update (README + quick reference where relevant),
  - diagnostics update,
  - repair/error behavior validation,
  - at least one negative-path test per write feature.

---

## Out-of-scope for now

- Direct adoption of GPL implementation code from reference projects
- Non-local/cloud control paths
- Bulk write orchestration across multiple inverters in one transaction
