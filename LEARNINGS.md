# KOSTAL KORE — Consolidated Learnings (2026-03)

This file captures the most important implementation learnings from the recent
backlog execution wave and follow-up validation.

## 1) Network auto-discovery is possible, but best-effort

### What is true now
- Auto-discovery **can work** when the setup host field is empty.
- The setup flow probes likely local IPv4 candidates and then validates
  credentials against discovered hosts before accepting one.
- Discovery is therefore a convenience path, not a hard requirement.

### Why it can fail
- VLAN / subnet isolation
- firewall rules (port 80 probes or API access blocked)
- unusual network topology / routing
- inverter not reachable during setup window

### Decision / guidance
- Keep auto-discovery enabled as best-effort UX.
- Always keep manual host entry as first-class fallback.

---

## 2) Battery charge/discharge writes are Modbus-only by policy

### What we learned
In practice, REST battery charge/discharge controls are not consistently
reliable across firmware/device combinations. Accepting those writes in UI can
look successful while not producing deterministic inverter behavior.

### Implemented decision
- REST write paths for battery charge/discharge setpoints are intentionally
  blocked.
- Equivalent controls remain available via Modbus entities where behavior is
  deterministic and already safety-gated.

### Operational consequence
- If a user needs active charge/discharge power control, they must enable
  Modbus and use Modbus-backed entities.

---

## 3) High-impact write safety needs multiple guardrails

Single checks were not enough. Reliable safe behavior now requires:
- explicit write allowlist
- installer access validation
- temporary arming window for high-impact controls
- cross-field validation (e.g., off-threshold < on-threshold)
- read-after-write verification where readback API is available

### Important nuance
- Some test/legacy clients do not implement readback APIs.
- For those, strict verification paths must fail safely for high-impact writes
  and degrade compatibly for low-impact writes.

---

## 4) Event intelligence should be isolated from core polling

### Implemented pattern
- Events run in their own coordinator.
- Bounded ring buffer + dedup/cooldown avoids noise and memory growth.
- Rich event detail is diagnostics-only, not dumped into entity attributes.

### Outcome
- Actionable event context added without destabilizing normal sensor polling.

---

## 5) Capability and polling behavior should adapt at runtime

### Learnings applied
- Cache capability probes (settings/process maps) to reduce startup duplication.
- Use adaptive polling backoff on repeated communication failures with jitter.
- Persist Modbus unavailable-register knowledge to reduce repeated log spam.

### Outcome
- Better resilience under busy/error periods.
- Lower repeated probe overhead.

---

## 6) KSEM should be optional and isolated

### Implemented pattern
- KSEM runs behind its own coordinator and config options.
- Failure domain is separate from inverter REST/Modbus.
- Source precedence is explicit:
  1. KSEM (if healthy)
  2. inverter powermeter (Modbus)
  3. REST fallback
- Source conflict/confidence is exposed as sensor attributes.

---

## 7) Documentation must track behavioral policy, not only features

Most valuable documentation updates were policy-level:
- what discovery can/cannot guarantee
- which write paths are intentionally disabled
- which controls are Modbus-only by design
- which safeguards are mandatory

This prevents future regressions where convenience reintroduces unsafe behavior.

---

## 8) Legacy migration is safest as a two-layer workflow

### Recommended operational order
1. **Adopt legacy entity IDs** (registry-only, safe/default path).
2. **Copy/merge recorder history** only for remaining unmatched pairs.

### Why this order matters
- Recorder history in Home Assistant is keyed through metadata/entity mappings.
- If canonical IDs can be preserved first, most historical continuity is retained
  without direct recorder DB changes.
- DB-level history merge should be a second step, not the default.

### Implemented safeguards
- Both migration services support `dry_run` previews.
- Apply mode requires multi-step confirmation:
  - challenge code generation,
  - code verification,
  - explicit final confirmation call.

---

## 9) Recorder history migration should update metadata, not raw row copies

### Implemented approach
- Merge at metadata layer (`states_meta`, `statistics_meta`) and move linked
  rows (`states`, `statistics`, `statistics_short_term`) with dedupe on
  conflicting timestamps/start buckets.

### Why this matters
- Avoids fragile row-by-row blind copies.
- Reduces duplicate-statistics artifacts and keeps recorder references coherent.
- Works across SQLite and MariaDB/MySQL (and PostgreSQL support path included).

---

## 10) Grid Feed-In Optimizer is a limiter, not PV curtailment

### Behavior
- Optimizer adjusts battery charge limit via Modbus register `1038`
  (`bat_max_charge_limit`) so PV surplus above configured grid feed-in cap is
  redirected to battery charging.
- When disabled, normal charge limits are restored.

### Important integration rule
- Register `1038` can be written by several control features.
- Running multiple concurrent writers (optimizer, external automations, manual
  scripts) can cause control contention. One owner per control period is safer.

---

## 11) `Isolation Resistance = unknown` is typically a data-source symptom

### What it means
- Isolation resistance is read from Modbus register `120`.
- `unknown` usually indicates:
  - no successful read after startup yet,
  - transient Modbus disconnects,
  - unsupported register on specific firmware/model (illegal address path).

### Operator guidance
- Verify Modbus connectivity first (host/port/unit-id and inverter setting).
- Check logs for `Connection lost reading isolation_resistance` or
  `Illegal data address`.

---

## 12) Modbus proxy error codes must match failure semantics

### What we learned
Returning Modbus exception **0x02** (Illegal Data Address) for transient
forwarding failures (backend disconnect, timeout) causes well-behaved clients
to permanently remove the register from their polling list. The register is
valid — the backend is temporarily unavailable.

### Implemented decision
- Forward-to-inverter failures now return **0x04** (Server Device Failure).
- 0x02 is reserved for truly unknown/unsupported register addresses.
- Unit-ID mismatches return **0x0B** (Gateway Target Device Failed to Respond).

### Consequence
External clients (evcc, SolarAssistant) will retry on 0x04 instead of
blacklisting valid registers.

---

## 13) FC16 frame validation must be strict at protocol boundary

### What we learned
The Modbus TCP proxy is a protocol boundary — external clients send raw frames.
Trusting `byte_count` from the frame without cross-checking `quantity * 2`
allows inconsistent payloads to reach decode/forward paths.

### Implemented decision
- Validate `quantity` range (1–123 per Modbus spec).
- Enforce `byte_count == quantity * 2` before processing.
- Reject early with 0x03 (Illegal Data Value) on mismatch.

---

## 14) Platform-forwarding failure needs explicit rollback

### What we learned
`async_forward_entry_setups()` can fail after the integration has already
started long-lived runtime objects (Modbus proxy, MQTT bridge, SoC controller,
authenticated API session). HA does **not** call `async_unload_entry()` on
setup failure — only on explicit unload/reload.

### Implemented decision
- Added `_rollback_setup()` that mirrors the cleanup pattern from
  `async_unload_entry()` but runs in the setup-failure path.
- Cleans up: SoC controller, Modbus proxy, MQTT bridge, plenticore session,
  and `hass.data` entry.

---

## 15) Config flow discovery must not spray credentials

### What we learned
The original `_probe_tcp_port()` in the discovery flow attempted a raw TCP
connect to every candidate IP. Replacing it with `_probe_kostal_api()` using
the unauthenticated `/api/v1/info/version` endpoint confirms the target is
actually a Kostal inverter without sending credentials to arbitrary hosts.

### Security consequence
- Credentials are only sent to confirmed Kostal devices.
- Discovery still works on non-standard ports if the API endpoint responds.

---

## 16) NaN/Inf from inverter firmware must be filtered at format boundary

### What we learned
Some firmware versions or transient Modbus read errors can produce `NaN` or
`Inf` float values. Propagating these to HA entity state causes downstream
issues (statistics, graphs, automations with numeric comparisons).

### Implemented decision
- `format_float()` and `format_energy()` return `None` for `NaN`/`Inf`.
- This makes the entity state `unknown` in HA, which is the correct semantic.

---

## 17) Legacy migration device identifiers are not future-proof

### What we learned
The current migration uses `add_config_entry_id` to link the new `kostal_kore`
entry to a device whose identifiers are still `("kostal_plenticore", serial)`.
Home Assistant announced (July 2025) that cross-domain device linking will
stop working in Core **2026.8** when device identifiers become domain-scoped.

### Current status
- Migration works today (HA 2025.x / early 2026.x).
- After HA 2026.8, the device side of the migration will silently fail.
- Unique-ID rewriting only covers `entry_id`-prefixed patterns.

### Planned remediation
See `MIGRATION_ARCHITECTURE.md` for the full plan:
1. Rewrite device identifiers during migration.
2. Make migration transactional (snapshot → migrate → rollback on failure).
3. Handle all known unique-ID naming patterns.

---

## 18) Codex static analysis: validation workflow

### Process that worked well
1. Receive Codex findings as a batch (typically 4 per file group).
2. Read the actual code at the cited lines.
3. Validate each finding against real code — reject false positives immediately.
4. Implement only confirmed-valid fixes.
5. Run tests, commit, push.

### False positive patterns observed
- Findings about missing error handling where try/except already existed.
- Findings about missing cleanup where the cleanup was added in a prior session.
- Findings about race conditions that are impossible in asyncio single-threaded context.
- Duplicate findings that describe the same root cause from different angles.

### Rejection rate
~30% of findings were invalid (already fixed, false positive, or not applicable).
Always validate before implementing.
