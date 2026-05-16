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
import json

sys.path.insert(0, "pykoplenti-master")

import aiohttp
from pykoplenti import ApiClient


async def main(host: str, password: str) -> None:
    async with aiohttp.ClientSession() as session:
        client = ApiClient(session, host)
        await client.login(password)
        print(f"✅ Eingeloggt auf {host}\n")

        # ── Bug #1: Battery-Einheit ──────────────────────────────────────────
        print("=" * 60)
        print("BUG #1 CHECK — devices:local:battery")
        print("=" * 60)
        try:
            bat = await client.get_process_data_values(
                "devices:local:battery",
                ["FullChargeCap_E", "WorkCapacity", "SoC", "Cycles"],
            )
            bat_data = bat.get("devices:local:battery", {})

            soc   = bat_data.get("SoC")
            cap_e = bat_data.get("FullChargeCap_E")
            work  = bat_data.get("WorkCapacity")
            cyc   = bat_data.get("Cycles")

            print(f"  SoC (State of Charge):   {soc.value if soc else 'n/a'} %")
            print(f"  FullChargeCap_E (raw):   {cap_e.value if cap_e else 'n/a'}")
            print(f"  WorkCapacity    (raw):   {work.value if work else 'n/a'}")
            print(f"  Cycles          (raw):   {cyc.value if cyc else 'n/a'}")
            print()

            if cap_e is not None and soc is not None:
                raw_cap = float(cap_e.value)
                soc_pct = float(soc.value)
                # Wenn raw_cap in Wh: typisch 5.000–20.000 für Heimspeicher
                # Wenn raw_cap in Ah: typisch 50–200 für Heimspeicher (bei 48V)
                if raw_cap > 500:
                    print(f"  → FullChargeCap_E = {raw_cap:.0f}")
                    print(f"    {'✅ Sieht aus wie Wh (>500). Bitte bestätigen.' if raw_cap > 500 else ''}")
                    if raw_cap > 1000:
                        print(f"    ⚠️  Falls das eine 10-kWh-Batterie ist: {raw_cap:.0f} Wh = {raw_cap/1000:.1f} kWh ← plausibel?")
                        print(f"    ⚠️  Als Ah wäre {raw_cap:.0f} Ah = {raw_cap * 48 / 1000:.0f} kWh (bei 48V) ← plausibel?")
                else:
                    print(f"  → FullChargeCap_E = {raw_cap:.1f}")
                    print(f"    ⚠️  Kleiner Wert — sieht aus wie Ah (50–200 Ah typisch für 48V-Batterie)")
        except Exception as e:
            print(f"  ❌ Fehler: {e}")

        # ── Bug #11: PV-Energiestatistik ─────────────────────────────────────
        print()
        print("=" * 60)
        print("BUG #11 CHECK — scb:statistic:EnergyFlow (alle Schlüssel)")
        print("=" * 60)
        try:
            # Erst alle verfügbaren Keys abfragen
            all_modules = await client.get_process_data()
            energy_keys = all_modules.get("scb:statistic:EnergyFlow", [])

            pv_energy_keys = sorted(
                k for k in energy_keys if "EnergyPv" in k
            )
            other_energy_keys = sorted(
                k for k in energy_keys if "EnergyPv" not in k
            )

            print(f"\n  Gefundene EnergyPv-Schlüssel ({len(pv_energy_keys)}):")
            for k in pv_energy_keys:
                print(f"    {k}")

            print(f"\n  Andere EnergyFlow-Schlüssel ({len(other_energy_keys)}):")
            for k in other_energy_keys[:20]:  # Erste 20
                print(f"    {k}")
            if len(other_energy_keys) > 20:
                print(f"    ... und {len(other_energy_keys) - 20} weitere")

            # Aktuelle Werte der PV-Schlüssel holen
            if pv_energy_keys:
                print()
                vals = await client.get_process_data_values(
                    "scb:statistic:EnergyFlow", pv_energy_keys
                )
                pv_vals = vals.get("scb:statistic:EnergyFlow", {})
                print("  Aktuelle PV-Energiewerte (heute):")
                for k in sorted(k for k in pv_energy_keys if ":Day" in k):
                    v = pv_vals.get(k)
                    print(f"    {k}: {v.value if v else 'n/a'}")

            # Fazit
            max_pv_num = 0
            for k in pv_energy_keys:
                for i in range(1, 10):
                    if f"EnergyPv{i}" in k:
                        max_pv_num = max(max_pv_num, i)
            print()
            if max_pv_num <= 3:
                print(f"  ✅ API liefert nur EnergyPv1–{max_pv_num}: Bug #11 ist KEIN Bug für dieses Gerät.")
                print(f"     (Kostal-API unterstützt offenbar nur {max_pv_num} PV-Strings in EnergyFlow)")
            else:
                print(f"  ❌ API liefert EnergyPv1–{max_pv_num}: Bug #11 ist REAL — Sensor-Definitionen fehlen!")
        except Exception as e:
            print(f"  ❌ Fehler: {e}")

        # ── DC-String-Anzahl aus Settings ─────────────────────────────────────
        print()
        print("=" * 60)
        print("DC-STRING-ANZAHL — devices:local Properties:StringCnt")
        print("=" * 60)
        try:
            setting = await client.get_setting_values(
                "devices:local", "Properties:StringCnt"
            )
            cnt = setting.get("devices:local", {}).get("Properties:StringCnt")
            print(f"  Properties:StringCnt = {cnt.value if cnt else 'n/a'}")
        except Exception as e:
            print(f"  ❌ Fehler: {e}")

        print()
        print("Fertig. Bitte die Werte oben mit deiner Batterie-Spezifikation vergleichen.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Verwendung: python3 check_inverter_api.py <HOST> <PASSWORT>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1], sys.argv[2]))
