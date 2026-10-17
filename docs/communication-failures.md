# Communication failures and diagnosis

The proxy serves recent **observations**, separately from entity/command caches.
It invalidates observations on failed/partial polls, reconnects and writes. Fast
values expire after three polling intervals (at least 15 seconds); slow values
have a six-times-longer allowance. A miss requires a real upstream read. Failure
returns Modbus exception 04, not old data or a fabricated zero. Short responses
are rejected and each request has a 10 second processing deadline.

A failed write has an **unknown outcome**: the inverter may have received it
before the reply was lost. The proxy counts uncertain writes and does not queue
them for replay. Existing installer/SoC-controller/register-owner arbitration
still applies. A readable limit is not evidence that all external battery
controllers can safely own the same battery at once.
`pending_write_confirmations` counts affected register words: a confirmed shorter
write only clears its own words from an earlier uncertain FC16 range.

G3 cyclic entity limits require readback before starting their keepalive. A new
command cancels the previous keepalive before attempting its replacement. Failed
writes, lost communication epochs and changed Modbus command ownership halt the
old lease, with a visible notification. It does not automatically resurrect when
the connection recovers. Native firmware fallback limits can still take effect;
configure and verify them on the actual device. Preferred grid-source selection
also excludes failed/retained sources and non-finite readings.
REST numeric commands and numeric readback confirmation must also be finite.
Unknown switch values are exposed as unknown rather than a plausible OFF state.

Essential REST process/settings failures keep last values in the display cache,
but the affected process sensors and setting controls become unavailable. This
lets consumers such as Inverter Charge Night distinguish retained data from
current telemetry. Modbus and REST incidents produce scoped persistent Home
Assistant notifications, retaining recovery receipts and bounded history. Those
histories survive integration restart. Proxy counters/history cover its current
runtime. Repeated polls do not create additional notifications; an ongoing
incident is reminded daily.
Recovery waits until platform discovery has finished and every active REST
polling coordinator has returned a successful round trip. Disabled/removed
readers are retired; late responses cannot resurrect their health channel.
Shutdown and integration reload do not turn an outstanding incident into a
recovery notification just because their readers are being removed.

In the integration options, **Expected proxy traffic timeout** enables an
aggregate client-traffic watchdog. Default 0 disables it: an unused proxy and a
client that normally closes TCP sessions are not outages. Choose a timeout
comfortably longer than the external application's maximum polling interval.
The watchdog reports missing requests and recovers on new valid requests; it
does not identify individual applications or change device setpoints. If several
clients use the proxy, traffic from one can mask the absence of another.

Use `kostal_kore.communication_diagnostics` in Developer Tools → Actions, with
response enabled. It returns local REST/Modbus incident histories, observed-cache
count, proxy read/cache/error/timeout/uncertain-write counters and request age.
It performs no network requests, file writes or inverter commands. The same
health snapshot is included in downloaded integration diagnostics and existing
debug bundles. New communication fields contain bounded fault codes, counts and
timestamps, without credentials, peer addresses or raw exception text.

For detailed tracing, temporarily enable debug logging for
`custom_components.kostal_kore`; reproduce once, then disable it and inspect the
download before sharing. Debug logs can contain more device detail than the
bounded communication snapshot.

These software mechanisms do not replace inverter/BMS protection, circuit
protection or device-local watchdogs. With Home Assistant or all control links
offline, the last inverter/charger setpoint can continue. Home Assistant cannot
deliver an outage notification while Home Assistant itself is down; detecting
that case requires independent supervision. Device timeout behavior must be
verified against actual firmware before claiming a guaranteed physical stop.

The paired ICN lab checks the unmodified official evcc binary through this actual
proxy, real Home Assistant and both integrations against a loopback inverter.
No real electrical installation is connected.
