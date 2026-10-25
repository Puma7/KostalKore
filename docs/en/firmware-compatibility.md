# Firmware compatibility and register 1024

**English** · [Deutsch](../de/firmware-compatibility.md)

## Verified manufacturer references

- [G3 release notes, 3.07.03.27419](https://www.kostal-solar-electric.com/fileadmin/downloadcenter/kse/Software/PLENTICORE_G3/Release_notes_G3_3.07.03.27419.txt), dated 2026-10-14: fixes ignored external-battery commands on register 1024. It also extends G3 battery-type register 588 to 32 bits while retaining 16-bit read compatibility.
- [Modbus protocol revision 2.9](https://www.kostal-solar-electric.com/fileadmin/downloadcenter/kse/BA/Protokollbeschreibung/BA_KOSTAL_Interface_MODBUS-TCP_SunSpec_with_Control.pdf), dated 2026-07-17, sections 3.4-3.5: 1024 is a signed 16-bit, write-only AC battery setpoint (FC06), scaled by read-only register 1025. Negative power requests charging; positive power requests discharging.

The release notes name a corrected release, not an exhaustive affected-version
range. Do not infer that every older generation/build has the same defect.

## REST setting reads on G3 3.07.03.27419

Field reports against the official integration show that this firmware answers
**single-setting** REST reads (`GET /settings/{module}/{id}`, pykoplenti's
`get_setting_values("module", "id")`) with `404 Module or setting not found`, while
the bulk form (`POST /settings`, `get_setting_values({"module": [ids]})`) keeps
working. The hostname setting is also returned as JSON
(`{"Hostname":"scb-5b162a"}` in #187116) instead of the plain name. Sources: home-assistant/core
[#186638](https://github.com/home-assistant/core/issues/186638),
[#187116](https://github.com/home-assistant/core/issues/187116) and
[#187002](https://github.com/home-assistant/core/issues/187002). Kostal has not
documented this change, so it was not verified on KORE hardware.

KORE therefore uses only the `{module: [ids]}` form for every setting read (setup
connection test, string-count discovery, shadow management, diagnostics,
on-demand reads) and decodes a JSON-encoded hostname. A test rejects any
reintroduced single-setting call. Process data already used the bulk form.

## Current KORE and ICN behavior

Neither KORE's battery controller nor ICN currently uses 1024 for charging.
KORE's legacy descriptor still blocks writes to that address through its
`Access.RO` classification; that classification is a conservative implementation
restriction, not a statement about the current manufacturer's protocol.
The register is excluded from normal monitoring and writable-entity discovery.
The existing descriptor must be revisited before implementing a 1024 writer.

KORE uses existing DC-command/charge-limit paths such as 1034 and 1038, and
separate G3 cyclic limits where configured. ICN uses the entities selected in
its configuration. Register 1024 is not required to keep those paths working.
A limit such as 1038 is not the same operation as a directional power command.

| Firmware situation | Current integration policy |
|---|---|
| Older G3 firmware | Retain existing supported control paths; do not introduce a 1024 dependency. |
| G3 3.07.03.27419 | The documented 1024 fix is known; no automatic control-path change occurs. REST setting reads use only the bulk form (see above). |
| Unknown firmware or another generation | Keep existing paths and observed-feedback checks; do not assume the G3 fix applies. |

An inverter firmware update is therefore not a prerequisite for the Wh-counter
quality fix in KORE 3.0.7. That defect is in KORE's magnitude validation, not in
register 1024 or the inverter's energy counters. Wh totals above 10 MWh are
valid; the bounded allowance applies only to ENERGY/Wh registers, not to power
or battery-capacity readings. Non-finite, negative and excessive totals remain
invalid and cannot renew confirmed control feedback.

## Firmware identification and future enablement

Use the inverter's overall `sw_version`, read from Modbus register 58 or REST
`get_version().sw_version`. Register 586 identifies the battery firmware, while
registers 38/46 identify the MC/IOC components; those are different version
namespaces and must not be compared to the G3 overall-release threshold.

KORE already parses the major/minor/patch through `parse_firmware_version`
and `firmware_at_least`. These helpers deliberately omit a fourth build number.
They cannot alone attest a precise fixed build. Any future 1024 implementation
needs an explicit generation/capability decision and full-build handling where
the manufacturer's guarantee is build-specific. Missing or malformed versions
must leave that optional path disabled while retaining existing supported paths.

A future writer also needs the signed scale from 1025, FC06 encoding, range and
permission checks, suitable independent device feedback, coexistence arbitration,
and tests on affected and corrected firmware. A firmware number is not proof that
a command was applied. Implementing or selecting this new path is deferred until
those requirements have been validated on hardware.

The persisted unsupported-register cache already includes the complete overall
firmware string in its signature. During integration setup, a cache with a
different firmware signature is not restored. This is capability rediscovery,
not permission to select a different battery-control protocol automatically.

For register 588, the present UINT16/count=1 reader can still read the register
in both documented formats, but it cannot identify battery types whose code
needs the extended 32-bit format. A future extended reader must be restricted to the
verified generation/firmware and validated for word layout and model mapping;
it must not silently change reads for older devices.
