# HA-Annahmen

<!-- Vorlage aus repo-vorlagen/vorlagen/ha-annahmen.md. Der HA-Beta-Bericht (repo-vorlagen/ha-beta-bericht.md)
     gleicht diese Liste jeden Monat mit den Neuerungen der HA-Beta ab. Nur festhalten, was bei einer Änderung in HA
     still kaputtginge. -->

Worauf sich dieses Repo bei Home Assistant verlässt. Bei jeder Änderung am Code mitpflegen.

**Getestet mit:** HA 2026.9.3 (Test-venv), CI-Frühwarnung gegen die neueste HA · **Mindestversion:** 2026.9.0 (`hacs.json`)

## Annahmen

| Annahme | Wo im Code | Abgesichert durch |
|---|---|---|
| HAs Bleak-Wrapper (`BleakClientWithServiceCache` aus bleak_retry_connector, zur Laufzeit von HA ersetzt) wählt den Proxy in der **privaten** Methode `_async_get_best_available_backend_and_device(manager)` und hat `_async_get_backend_for_ble_device(manager, scanner, device)`. Fehlt sie, gibt es eine Warnung und die automatische Wahl. | `ble.py` `_client_class` | `tests/test_proxy.py` `test_preferred_first_then_automatic`, `test_missing_private_api_warns_once` |
| Der verbundene Client hat das private Attribut `_connected_scanner` mit `name` und `source` (Proxy der laufenden Verbindung); sonst Rückfall auf `device.details["source"]` + `async_scanner_by_source`. | `ble.py` `_source_name`, `_source_id` | `tests/test_proxy.py` `test_source_name_and_id_fallbacks` |
| `bluetooth.async_current_scanners` liefert Scanner mit `source`, `name`, `connectable`; `async_scanner_devices_by_address(..., connectable=True)` liefert Treffer mit `scanner` und `advertisement.rssi`. | `config_flow.py` `_proxy_options`, `ble.py` | `tests/test_proxy.py` `test_options_list_proxies` |
| `service.async_register_platform_entity_service` (Dienste an der Licht-Plattform, auch mit `SupportsResponse.ONLY`). | `__init__.py` `async_setup` | `tests/test_integration.py` (`test_get_programs`, `test_set_pro_program`, …) |
| `async_get_translations(hass, lang, "selector", {DOMAIN})` liefert Schlüssel der Form `component.<domain>.selector.<key>.options.<option>` (Proxy-Beschriftungen, Grundname neuer Profile). | `config_flow.py` `_proxy_options`, `coordinator.py` `async_save_profile` | `tests/test_proxy.py` `test_options_labels_english`, `tests/test_profiles.py` `test_save_and_apply` („Profil 1“) |
| `OptionsFlowWithReload` lädt den Eintrag nur nach dem Options-Dialog neu; `async_update_entry(options=…)` von außen (Knopf „Programm als Profil speichern“) lädt **nicht** neu, die Verbindung bleibt. | `config_flow.py`, `coordinator.py` `async_save_profile` | `tests/test_profiles.py` `test_save_and_apply` (gleicher Coordinator, keine neue Verbindung) |
| Options-Menü per `async_show_menu(menu_options=[…])`: HA springt selbst in `async_step_<option>`; ohne Profile öffnet der Dialog direkt den Schritt `settings`. | `config_flow.py` `FluvalOptionsFlow` | `tests/test_profiles.py` `test_options_*`, `tests/test_config_flow.py` `test_options` |
| `SelectEntity.options` ist in HA eine `cached_property`; `FluvalProfile` überschreibt sie mit einer normalen `property`, damit neue Profile ohne Neuladen in der Auswahl erscheinen. | `select.py` `FluvalProfile` | `tests/test_profiles.py` `test_save_and_apply` (Optionen der Auswahl nach dem Knopf ohne Neuladen) |
| Zustände über 255 Zeichen schreibt HA nicht (Fehler im Log, Zustand „unknown“) → Profilnamen höchstens 100 Zeichen. | `const.py` `PROFILE_NAME_MAX`, `config_flow.py`, `profiles.py` | `tests/test_profiles.py` `test_options_name_length` |
| Entity-Registry `async_get_entity_id("light", DOMAIN, unique_id)` für das Präfix neuer Entity-IDs. | `__init__.py` `_object_prefix` | `tests/test_integration.py` (Fixture `registered`) |

## Fremde Integrationen

- `weather` (beliebige Wetter-Entität aus den Optionen): Zustände `lightning`, `lightning-rainy`, `partlycloudy`,
  `cloudy` (`coordinator.py` `weather_effect`).

## Prüfbefehl

Frühwarnung gegen die neueste HA läuft montags in der CI (`.github/workflows/pruefung.yml`). Lokal:

```sh
.venv/bin/python -m pytest -q -p no:logging
```
