Phase 1 — Bronze finalisieren (S/M) ✅ erledigt
1) Entity Naming & Translation Keys (M) ✅
   - has_entity_name=True, translation_key für alle Entities
   - Entfernt redundante Präfixe, sauberer UI‑Name
2) Docs: Installation + Konfiguration + Entitätenliste (M) ✅
   - README/QUICK_REFERENCE konsistent
   - einfache Tabelle pro Plattform
3) quality_scale.yaml finalisieren (S) ✅
   - Regeln mit done/todo/exempt + Kommentar

Phase 2 — Silver (M/L) ✅ erledigt
4) Reauth‑Flow (M) ✅
   - ConfigFlow → async_step_reauth, async_step_reauth_confirm
5) Repair Issues (M) ✅
   - Auth‑Fail, 503, API‑Down als UI‑Issues
6) Logging‑Hygiene (S/M) ✅
   - 404/feature‑missing nur Debug/Info
   - klare Warnung bei echten Fehlern

Phase 3 — Gold‑Ziele (L) ⏳ in Arbeit
7) Discovery (L, optional)
   - mDNS/SSDP/DHCP prüfen (falls Gerät etwas broadcastet)
8) End‑User Docs auf HA‑Standard (M/L) ✅
   - Beispiele, Dashboard‑Screens, Troubleshooting
9) Entity Metadata Review (M) ✅
   - device_class/state_class/unit geprüft (Energy Dashboard)

Optional — Richtung Platinum (L)
10) Strikte Typisierung (L) ⏳ in Arbeit
11) Performance‑Dokumentation + Benchmarks (M)

Phase 5 - pytest --cov
12) Erziele 100% mit pytest --cov ✅