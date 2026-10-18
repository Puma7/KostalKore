# Diagnostic reports

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

The action supports optional response data and returns `bundles` (download URLs)
and `errors`. It also posts a persistent notification with clickable links. Files
are written to `www` inside the **actual HA configuration directory**, which need
not be `/config`. If `www` was created for the first time after frontend startup,
Home Assistant needs a restart to serve `/local` links. The standard integration
diagnostic download works independently of this directory and does not require
that restart. Files in `www` are static files: remove exported reports after use.

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
