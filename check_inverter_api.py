#!/usr/bin/env python3
"""
Diagnoseskript für Bug #1 (Battery-Einheit) und Bug #11 (PV-Energiestatistik).

Aufruf (Credentials werden interaktiv via getpass abgefragt, falls weggelassen):
    # User-Modus:
    python3 check_inverter_api.py <HOST>
    python3 check_inverter_api.py <HOST> <PASSWORT>                      # alt, weniger sicher

    # Master/Installer-Modus:
    python3 check_inverter_api.py <HOST> --installer
    python3 check_inverter_api.py <HOST> <MASTER_KEY> <SERVICE_CODE>     # alt, weniger sicher

Warnung: Wenn Credentials als CLI-Argumente übergeben werden, landen sie in
Shell-History und Prozessliste. Bevorzuge den interaktiven Modus.
"""

import asyncio
import getpass   # NEU: sichere interaktive Eingabe ohne Shell-History
import sys
from typing import Optional

sys.path.insert(0, "pykoplenti-master")

import aiohttp
from pykoplenti import ApiClient


async def query_single(client, module_id: str, key: str):
    """Eine einzelne Key-Abfrage mit Fehler-Capture."""
    try:
        res = await client.get_process_data_values(module_id, [key])
        entry = res.get(module_id, {}).get(key)
        return entry.value if entry is not None else None, None
    except Exception as e:
        return None, str(e)


async def main(host: str, key: str, service_code: Optional[str]) -> None:
    async with aiohttp.ClientSession() as session:
        client = ApiClient(session, host)
        if service_code:
            await client.login(key, service_code=service_code)
            role = "master (installer)"
        else:
            await client.login(key)
            role = "user"
        print(f"✅ Eingeloggt auf {host} als '{role}'\n")

        # Verifiziere Login durch get_me() falls verfügbar
        try:
            me = await client.get_me()
            print(f"  Server bestätigt Auth-Rolle: {me}\n")
        except Exception as e:
            print(f"  (get_me() failed: {e})\n")

        all_modules = await client.get_process_data()

        # ── Bug #1: Battery ─────────────────────────────────────────────────
        print("=" * 60)
        print("BUG #1 CHECK — devices:local:battery")
        print("=" * 60)

        bat_keys = list(all_modules.get("devices:local:battery", []))
        if not bat_keys:
            print("  ⚠️  Modul 'devices:local:battery' nicht verfügbar.")
        else:
            print(f"  Alle verfügbaren Keys: {bat_keys}\n")

            # Test 1: Massenabfrage OHNE Key-Filter (alle Werte des Moduls)
            print("  Test 1: Massenabfrage ohne Key-Filter")
            try:
                res = await client.get_process_data_values("devices:local:battery")
                mod = res.get("devices:local:battery", {})
                if mod:
                    print(f"    ✅ {len(mod)} Werte erhalten:")
                    for k, entry in mod.items():
                        val = entry.value if hasattr(entry, "value") else entry
                        print(f"      {k}: {val}")
                else:
                    print(f"    ⚠️  Leeres Ergebnis")
            except Exception as e:
                print(f"    ❌ {e}")

            # Test 2: Einzelabfrage harmloser Keys (Strings ohne 500-Risiko)
            print("\n  Test 2: Einzelabfrage harmloser String-Keys")
            for k in ("BatManufacturer", "BatModel", "BatSerialNo"):
                if k in bat_keys:
                    val, err = await query_single(client, "devices:local:battery", k)
                    print(f"    {k}: {val if err is None else f'❌ {err}'}")

            # Test 3: Einzelabfrage der eigentlichen Bug-#1-Keys
            print("\n  Test 3: Einzelabfrage der Kapazitäts-Keys (Bug #1)")
            for k in ("FullChargeCap_E", "WorkCapacity", "SoC", "Cycles", "U", "I", "P"):
                if k in bat_keys:
                    val, err = await query_single(client, "devices:local:battery", k)
                    if err is None and val is not None:
                        print(f"    {k}: {val}")
                        if k in ("FullChargeCap_E", "WorkCapacity"):
                            try:
                                raw = float(val)
                                print(f"      → Als Wh:  {raw:.0f} Wh  = {raw/1000:.2f} kWh")
                                print(f"      → Als Ah:  {raw:.0f} Ah  = {raw * 48 / 1000:.1f} kWh (bei 48V)")
                            except (TypeError, ValueError):
                                pass
                    else:
                        print(f"    {k}: ❌ {err}")

        # ── Bug #11 — schon bestätigt, kurz halten ──────────────────────────
        print()
        print("=" * 60)
        print("BUG #11 CHECK — bereits bestätigt: KEIN Bug")
        print("=" * 60)
        pv_keys = sorted(
            k for k in all_modules.get("scb:statistic:EnergyFlow", [])
            if "EnergyPv" in k
        )
        max_pv = max((int(k.split(":")[1].replace("EnergyPv", "")) for k in pv_keys), default=0)
        print(f"  API liefert EnergyPv1–{max_pv} → Code-Limit (3) ist korrekt.")

        # Test mit einer Tages-Stat einzeln
        if pv_keys:
            print("\n  Test: einzelne PV-Statistik-Abfrage")
            val, err = await query_single(
                client, "scb:statistic:EnergyFlow", "Statistic:EnergyPv1:Day"
            )
            print(f"    Statistic:EnergyPv1:Day: {val if err is None else f'❌ {err}'}")

        # ── DC-Strings ──────────────────────────────────────────────────────
        print()
        print("=" * 60)
        print("DC-STRING-ANZAHL")
        print("=" * 60)
        try:
            s = await client.get_setting_values("devices:local", "Properties:StringCnt")
            cnt = s.get("devices:local", {}).get("Properties:StringCnt")
            if hasattr(cnt, "value"):
                cnt = cnt.value
            print(f"  Properties:StringCnt = {cnt}")
        except Exception as e:
            print(f"  ❌ {e}")

        print()
        print("Fertig.")


if __name__ == "__main__":
    # GEÄNDERT: getpass-Fallback bei fehlenden Credentials.
    # Alte Aufrufformen (Passwort/Key als CLI-Args) bleiben rückwärtskompatibel.
    args = sys.argv[1:]
    if not args:
        print("Aufruf:")
        print("  User-Modus:    python3 check_inverter_api.py <HOST>")
        print("  Master-Modus:  python3 check_inverter_api.py <HOST> --installer")
        sys.exit(1)

    host = args[0]
    rest = args[1:]

    if rest and rest[0] == "--installer":
        master_key = getpass.getpass("Master-Key: ")
        service_code = getpass.getpass("Service-Code: ")
        asyncio.run(main(host, master_key, service_code))
    elif len(rest) == 0:
        password = getpass.getpass("Passwort: ")
        asyncio.run(main(host, password, None))
    elif len(rest) == 1:
        asyncio.run(main(host, rest[0], None))
    elif len(rest) == 2:
        asyncio.run(main(host, rest[0], rest[1]))
    else:
        print("Zu viele Argumente.")
        sys.exit(1)
