# Diagnostic reports

**English** · [Deutsch](../de/diagnostics.md)

Prefer Home Assistant's integration diagnostic download: **Settings → Devices &
services → Kostal KORE → entry menu → Download diagnostics**. From 3.0.8 this also
includes `data.runtime`: cached Modbus readings with their word order and stale
markers, plus each REST polling channel's requested, missing and fresh field IDs.
Settings values and credentials are not included in the channel summary.

Collection of this runtime section uses local state only. A cached value is not
proof of a current successful read; inspect `slow_poll_stale`,
`retained_quality_addresses` and the communication state alongside it. REST
`missing_fields` compares against the displayed cache, which may contain fallback
values; `fresh_fields` and `communication_stale` distinguish that case. A null
stale flag means that the channel has not completed its first update.

For the additional health summary and write audit, execute:

```yaml
action: kostal_kore.export_debug_bundle
data: {}
```

The action is restricted to administrators. It supports optional response data
and returns `bundles` (download URLs) and `errors`. The persistent notification
lists only the file names: notifications are visible to every HA user, and the
signed links work without a login, so they are only part of the action response
(Developer Tools → Actions, *Response*). Files are written to `kostal_kore_debug` inside
the **actual HA configuration directory** (which need not be `/config`) — not to
`www`, because `/local` serves files without authentication. Downloads go through
`/api/kostal_kore/debug_bundle/…` and require an administrator; the returned links
are signed for the calling user and expire after one hour. Exported files are
deleted after 24 hours, also after the last KORE entry was removed. Links are signed
with the refresh token of the caller's WebSocket or HTTP session. An action started
outside the UI/API (e.g. by an automation) has none and returns no link, only the
file name (`url: null`): copy the file from the directory above, or run the action
from Developer Tools → Actions.
Bundles exported to `www` by earlier versions stay publicly reachable until
you delete `www/kore_debug_*.json`.

## Interpreting the battery limits

According to [KOSTAL protocol revision 2.9, section 3.4](https://www.kostal-solar-electric.com/fileadmin/downloadcenter/kse/BA/Protokollbeschreibung/BA_KOSTAL_Interface_MODBUS-TCP_SunSpec_with_Control.pdf),
registers **1038 / 1040 are absolute charge / discharge power limits in watts**.
They are not current battery power, percentages or the battery's hardware maximum
(1076 / 1078). KORE displays the decoded value unchanged; the number entity's
100-W input step does not round incoming readings. Both configured FLOAT32 word
orders are supported. The same word order also applies to the Min/Max-SoC float
registers 1042 / 1044.

A small fractional limit can therefore be represented correctly, but that alone
does not establish whether the inverter is enforcing it, whether another
controller set it, or whether the device returned it correctly. Compare a fresh
diagnostic snapshot, inverter WebUI, management mode and recent control writes.
Do not replace an unfamiliar value with a guessed default. A successful Modbus
startup probe or a recovered Modbus channel does not establish REST health.

## AC readings and separate control limits

`AC Power` and `AC L1/L2/L3 Current/Power` use the inverter's
`devices:local:ac` data. They describe the inverter AC connection, including its
PV/battery conversion, rather than whole-house consumption. An idle inverter can
therefore report zero current/power while the home imports power from the grid.
`Grid Power`, `Home Power` and the `devices:local:powermeter` readings describe
different measurement points; compare snapshots from the same time.

`Battery Max Charge/Discharge Power (SoC Ctrl)` are local controller settings,
initially 5,000 W (clamped to the inverter rating). They are used when that
controller runs and are not readbacks of registers 1038/1040 or G3 1280/1282.
The numeric Modbus entities display the actual decoded register observations.
An inactive control mode does not prove that a readback limit is being enforced.

Battery voltage reported as zero while the inverter/battery interface is idle
does not establish zero cell voltage. Confirm battery state and availability
with the inverter/BMS; KORE does not invent a replacement pack voltage.

## PV inputs and REST communication

The hardware DC-input count can include a battery connector. From 3.0.9, a
successful REST capability catalog determines which PV modules and fields are
subscribed, including for the PV-sum sensor. Unsupported PV inputs are not
requested. When catalog discovery fails, the physical-count fallback remains;
if an advertised input disappears during polling, the communication fault is
still reported. This does not replace missing PV data with zero.

## Retained isolation assessments

The health monitor can retain a previously recorded isolation sample, including
one restored from storage. In standby the live Modbus reading can be unavailable
while that retained sample still affects the health level and score.

From 3.0.10, `health_summary.isolation_measurement` and the isolation-warning
attributes expose `assessment_basis` (`current`, `historical`, `unavailable`),
`sample_origin` (`modbus`, `restored`, `none`), `current_measurement_ohm` and
`last_recorded_ohm`. The historical tracker value and warning are preserved;
`kostal_display_mohm` is null when there is no current measurement. An unavailable
live measurement does not establish that an earlier low value was erroneous.
Compare the next valid measurement during operation with the inverter's own
diagnostics before classifying the retained assessment.
Missing, invalid and quality-retained isolation responses also mark the live
measurement unavailable while retaining its previous assessment.

## Control acknowledgements

From 3.0.11 a readable Modbus number requires matching, finite device feedback
within three valid read attempts (0.2 seconds between differing numeric results).
Client read errors are not retried or immediately refreshed again: the client
already owns transport retry and register suppression. Floating-point precision
(`rel_tol=1e-6`, `abs_tol=1e-6`) is accepted, while zero requires exact feedback.
The UI input step is not used as a confirmation tolerance. Retained-quality
values or a changed connection generation cannot confirm the command. An
unconfirmed ordinary control refreshes observations and raises an error; G3
cyclic controls retain their existing no-keepalive-on-failure protection.
Register 533 is documented as write-only and has no readable confirmation.
A Modbus transport acknowledgement / audit `ok` alone is not evidence that
battery charging follows the requested power. Compare live telemetry as well.
Read-only and invalid number commands raise validation errors without writing.

The final coordinator refresh must also retain the original connection
generation and a healthy connected coordinator. A reconnect, disconnection or
failed refresh invalidates the earlier confirmation and cannot start a G3
keepalive lease. The refreshed register must still match the command and have
current quality: a newer different, missing, non-finite or retained reading
also invalidates confirmation. The next command may establish a new confirmation.
For monitored controls the poll is awaited directly, bypassing request debounce;
the per-register fresh observation is used rather than a merged slow-poll cache
or failures of unrelated registers. Non-monitored readable controls still use
their direct readback, and write-only register 533 retains its documented exception.

## Safety event logging

From 3.0.12 reading the PV System Safety state, icon or attributes does not
create additional warning messages. The safety monitor still logs new and
escalated alerts. Risk levels, unsafe state, thresholds and retained alerts
are unchanged. A repeatedly rendered alert is not a series of new events;
use its category, creation time and live measurements when investigating it.

## Overnight control context (3.0.13)

Both standard diagnostics and the debug bundle include
`runtime.modbus.device_info_cached`: an allowlist of cached inverter firmware,
product/state, rating and battery-management mode. This is discovery metadata,
not a fresh live confirmation of control mode. It may be absent before discovery
or retained through a connection outage; compare the connection/freshness markers
and confirm the current mode in the inverter WebUI when investigating conflicts.

Each REST channel exposes `control_settings` for selected numeric battery
settings already present in its cache: minimum SoC, manual charging, strategy,
smart/time control and external charge/discharge limits. Each entry has `value`
and `fresh`; invalid/non-finite values are null, and a retained value is not
current confirmation. Missing settings mean not collected, not disabled or zero.
The exporter does not subscribe to inactive settings or issue control commands.
Other settings and arbitrary string values are not added to this context.

Download both integration diagnostics, full debug logs, the additional bundle
and the HA history before restarting or reloading after the night. The bundle
contains only the last 100 in-memory Modbus write events, not the full night.
An audit `ok` records the transport write; the number-service readback result and
actual AC/DC power remain separate evidence.
