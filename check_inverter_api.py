#!/usr/bin/env python3
"""
Diagnoseskript für Bug #1 (Battery-Einheit) und Bug #11 (PV-Energiestatistik).

Aufruf:
    python3 check_inverter_api.py <HOST> <PASSWORT>

Beispiel:
    python3 check_inverter_api.py 192.168.1.100 "meinPasswort"
"""

import asyncio
import sys

sys.path.insert(0, "pykoplenti-master")

import aiohttp
from pykoplenti import ApiClient


async def main(host: str, password: str) -> None:
    async with aiohttp.ClientSession() as session:
        client = ApiClient(session, host)
        await client.login(password)
        print(f"✅ Eingeloggt auf {host}\n")

        # Alle verfügbaren Module + Keys holen (einmal, für alle Checks)
        all_modules = await client.get_process_data()

        # ── Bug #1: Battery-Einheit ──────────────────────────────────────────
        print("=" * 60)
        print("BUG #1 CHECK — devices:local:battery")
        print("=" * 60)

        bat_available_keys = list(all_modules.get("devices:local:battery", []))
        if not bat_available_keys:
            print("  ⚠️  Modul 'devices:local:battery' nicht verfügbar.")
            print("      Entweder keine Batterie angeschlossen oder Wechselrichter")
            print("      meldet das Modul nicht. Bug #1 kann nicht geprüft werden.")
        else:
            print(f"  Verfügbare Battery-Keys ({len(bat_available_keys)}): {bat_available_keys}")
            # Nur Keys anfragen die tatsächlich existieren
            wanted = [k for k in ["FullChargeCap_E", "WorkCapacity", "SoC", "Cycles"]
                      if k in bat_available_keys]
            if not wanted:
                print(f"  ⚠️  Keiner der gesuchten Keys vorhanden.")
            else:
                # Einzeln abfragen — bulk gibt manchmal 500 bei Plenticore
                results: dict[str, object] = {}
                for key in wanted:
                    try:
                        bat = await client.get_process_data_values(
                            "devices:local:battery", [key]
                        )
                        entry = bat.get("devices:local:battery", {}).get(key)
                        val = entry.value if entry is not None else "n/a"
                        results[key] = val
                        print(f"  {key}: {val}")
                    except Exception as e:
                        print(f"  {key}: ❌ {e}")
                        results[key] = None

                for label in ("FullChargeCap_E", "WorkCapacity"):
                    if results.get(label) in (None, "n/a"):
                        continue
                    try:
                        raw = float(results[label])
                    except (TypeError, ValueError):
                        continue
                    print()
                    print(f"  Interpretation {label} = {raw}:")
                    print(f"    Als Wh:  {raw:.0f} Wh  = {raw/1000:.2f} kWh")
                    print(f"    Als Ah:  {raw:.0f} Ah  = {raw * 48 / 1000:.1f} kWh (bei 48V-System)")
                    if raw > 500:
                        print(f"    → Wert >500: wahrscheinlich Wh (typisch 5.000–20.000 Wh)")
                    else:
                        print(f"    → Wert ≤500: wahrscheinlich Ah (typisch 50–200 Ah)")

        # ── Bug #11: PV-Energiestatistik ─────────────────────────────────────
        print()
        print("=" * 60)
        print("BUG #11 CHECK — scb:statistic:EnergyFlow")
        print("=" * 60)

        energy_keys = list(all_modules.get("scb:statistic:EnergyFlow", []))
        pv_keys = sorted(k for k in energy_keys if "EnergyPv" in k)

        print(f"\n  Gefundene EnergyPv-Schlüssel ({len(pv_keys)}):")
        for k in pv_keys:
            print(f"    {k}")

        max_pv_num = 0
        for k in pv_keys:
            for i in range(1, 10):
                if f"EnergyPv{i}" in k:
                    max_pv_num = max(max_pv_num, i)

        print()
        if max_pv_num == 0:
            print("  ⚠️  Keine EnergyPv-Schlüssel gefunden.")
        elif max_pv_num <= 3:
            print(f"  ✅ API liefert nur EnergyPv1–{max_pv_num}: Bug #11 ist KEIN Bug.")
            print(f"     Die Kostal-API unterstützt für dieses Gerät maximal {max_pv_num} PV-Strings")
            print(f"     in der Energiestatistik. Kein Fix nötig.")
        else:
            print(f"  ❌ API liefert EnergyPv1–{max_pv_num}: Bug #11 ist REAL!")
            print(f"     Sensor-Definitionen für EnergyPv4–{max_pv_num} fehlen im Code.")

        if pv_keys:
            print("\n  Aktuelle Tageswerte (einzeln abgefragt):")
            for k in sorted(k for k in pv_keys if ":Day" in k):
                try:
                    vals = await client.get_process_data_values(
                        "scb:statistic:EnergyFlow", [k]
                    )
                    v = vals.get("scb:statistic:EnergyFlow", {}).get(k)
                    print(f"    {k}: {v.value if v else 'n/a'}")
                except Exception as e:
                    print(f"    {k}: ❌ {e}")

        # ── DC-String-Anzahl aus Settings ─────────────────────────────────────
        print()
        print("=" * 60)
        print("DC-STRING-ANZAHL — Properties:StringCnt")
        print("=" * 60)
        try:
            setting = await client.get_setting_values(
                "devices:local", "Properties:StringCnt"
            )
            # get_setting_values gibt rohe Strings zurück, kein .value
            cnt = setting.get("devices:local", {}).get("Properties:StringCnt")
            if hasattr(cnt, "value"):
                cnt = cnt.value
            print(f"  Properties:StringCnt = {cnt}")
        except Exception as e:
            print(f"  ❌ Fehler: {e}")

        print()
        print("Fertig.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Verwendung: python3 check_inverter_api.py <HOST> <PASSWORT>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1], sys.argv[2]))
