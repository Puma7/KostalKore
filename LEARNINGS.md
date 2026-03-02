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
