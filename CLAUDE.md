# Fluval – Projektregeln

HA-Integration (`custom_components/fluvalble`, Domain `fluvalble`) für Fluval-Lampen der aktuellen Bluetooth-Generation
(Dienst FFF0, CBOR, 5 Kanäle; `products.py`: Reef-Typ 385, 546, 547 und Plant-Typ 386, 545, 548, 563). Entwickelt und
geprüft an einer Reef Nano 4.0 (Produkt 547); die übrigen Modelle sind nach App-Daten umgesetzt, aber ohne Hardware
geprüft. Nicht unterstützt: alte verschlüsselte Generation, AquaSky 3.0 (532, FACEBD), Roma & Shaker 2.0 (564, RGBW).
Nachfolger von MrMooreUK/fluvalble **mit gleicher Domain und gleichen unique_ids** – Einbau = Ordner tauschen,
Entity-IDs, Verlauf, Gerät, Automationen und Skripte bleiben. Für 547 darf sich bei Erweiterungen nichts ändern
(unique_ids, Entity-IDs, Kanalnamen, Verhalten). Übernommene Teile (Produkttabelle, Spektren, Farbumrechnung) in `NOTICE`.

- Repo ist **öffentlich**: nichts Persönliches ins Repo (keine Namen, echten MACs/IPs, Proxy-Namen, Heimnetz-Details,
  lokalen Pfade). Testdaten bleiben fiktiv (MAC 02:00:00:00:00:01, Proxys 02:00:00:00:00:A1/B2).
- README, CHANGELOG, Release-Notizen, Issue-Texte und Commit-Texte auf Englisch.
- Lokal committen ok, Push und Releases nur nach Rückfrage.

## Aufbau (`custom_components/fluvalble/`)
- `protocol.py` Dienst FFF0: schreiben FFF2, Rückmeldung FFF1. Befehl `D1`+CBOR-Map, Zustand holen `D0 FF`,
  Zustand `D2` (oder `D1`)+CBOR, über mehrere Pakete verteilt → `FrameAssembler`. Vollständig = ≥ 14 Schlüssel oder Modus+an/aus+alle 5 Kanäle
  (kleinere Maps sind Echos). Uhr `CD YY MM DD WD hh mm ss` (WD 1 = Montag). Schlüssel: 0 Firmware, 1 Modus,
  2 an/aus, 3–7 Kanäle (Reef Pink/Cyan/Blau/Violett/Kaltweiß, Plant Pink/Blau/Kaltweiß/Reinweiß/Warmweiß), 8–12 Automatik, 13 Profi, 14 Effekt (1–4), 15 Effekt-Zeitfenster
  (Bit 0 = **Sonntag**), 52 „find“. Quelle: FluvalConnect-APK (`eu/hagen/fluvalconnect/kxt/LightKxtKt.java`).
- `ble.py` Verbindung: ein Lock für alles, 0,2 s zwischen Befehlen, MTU-Stücke, `OPERATION_TIMEOUT` 75 s inkl.
  Warten; nach Zeitüberschreitung/Fehler Verbindung verwerfen. Nach Connect: Notify an, Uhr stellen.
- `coordinator.py` Abfrage 30 s (verbunden) bzw. 5 min (ohne „Verbindung halten“, Trennen nach 30 s Ruhe);
  Backoff ab 3 Fehlschlägen bis 5 min. Verfügbar nur mit echten Werten und letzter Meldung < 3 min (bzw. 12 min).
  `desired` (Store) = was HA zuletzt wollte; Abweichung → wiederherstellen (≥ 5 s nach eigenem Befehl, max. 1×/min).
  Erster Start ohne Store: Zustand der Lampe übernehmen. Befehle: Modus/an/Kanäle als **getrennte** Frames wie die App,
  danach `D0 FF` als Bestätigung, bei Abweichung 1× wiederholen, sonst `not_confirmed`.
  Kanäle > 0 schalten ein, alle 0 schalten aus. Einschalten ohne Werte = aktuelle/letzte Mischung, sonst Blau 20 %.
- `products.py` Produkttabelle (ID → Modell, Kanäle, Spektrum, Einschalt-Mischung); Modell aus `product_id` im Eintrag,
  sonst aus der Werbung (Firmen-ID FFFF/FF01, ID an Stelle 8–9), sonst 547.
- `light.py` RGB über Spektraldaten des Modells (`color.py`), Effekte als snake_case-Schlüssel (alte Namen werden angenommen).
- unique_ids `<MAC ohne Doppelpunkte>_<key>`: light, channel_1…5, mode, identify, sync_clock, connection (binary_sensor
  …_reachable), rssi, last_seen, active_connection_source; neu: hold_state, reconnect, timed_effects.
  Weggefallen: connection_mode. Neue Entity-IDs übernehmen das Präfix des Lichts (`entity.py`, `_object_prefix`).
- Eintrag Version 2.2 (alt 2.1 → Optionen auf `keep_connected` migriert).
- Logo: `brand/` aus der offiziellen SVG von fluvalaquatics.com (`assets/fluval-logo.svg`), mit chrome-headless-shell
  gerendert (Icon = nur das runde Zeichen).

## Version
Kalender-Schema **YY.M.N** ohne v (Jahr zweistellig, Monat ohne führende Null, N = laufende Nummer im Monat ab 1;
es zählen nur echte, veröffentlichte GitHub-Releases des Monats, auch alte X.Y.Z; bloße Tags nicht; ohne Release im Monat → .1; neuer Monat → wieder .1).
Jedes Release als GitHub-Release veröffentlichen (Tag + Release-Titel ohne v = genau die manifest-Version), sonst zählt es nicht mit.
Nächste Nummer immer aus `tools/next-version`, nie von Hand hochzählen, keine Patch-/Minor-Unterscheidung
(+ `CHANGELOG.md` `## YY.M.N – DD.MM.YYYY`, englisch). Die Versions-Badge im README und die Versionszeile der
Projektseite (`docs/index.html`) lesen die manifest.json selbst.

## Prüfen vor „fertig“
Eigene venv (HA 2026.9.3 + Bluetooth-Pakete): `uv venv --python 3.14 .venv` und
`uv pip install --python .venv/bin/python pytest-homeassistant-custom-component==0.13.366 bleak==3.0.2 bleak-retry-connector==4.7.0 bluetooth-adapters==2.4.0 bluetooth-auto-recovery==1.6.4 bluetooth-data-tools==1.29.24 dbus-fast==5.0.22 habluetooth==6.26.11 aiousbwatcher==1.1.2 serialx==1.10.0`
(oder `pip install -r requirements_test.txt`).
`.venv/bin/python -m pytest -q -p no:logging --cov=custom_components/fluvalble` (≥ 95 %),
`uvx ruff check . && uvx ruff format --check .`, `uvx mypy@2.3.1 --python-executable .venv/bin/python`,
hassfest: `docker run --rm -v "$PWD/custom_components:/github/workspace/custom_components" ghcr.io/home-assistant/hassfest`.
Tests mit `freezer` frieren auch die Loop-Zeit ein → Zeitgrenzen (`asyncio.timeout`) laufen dort nie ab;
Zeitüberschreitungen ohne `freezer` testen.
Übersetzungen: `.venv/bin/python tools/pruefe-uebersetzungen.py`. CI: `.github/workflows/pruefung.yml` (Vorlage
repo-vorlagen/vorlagen/ci-ha-integration.yml): ruff, mypy, Übersetzungen, pytest mit Abdeckungsgrenze 96 %, hassfest,
montags Frühwarnung gegen die neueste HA. Pakete der CI in `requirements_test.txt` (dieselben Versionen wie oben).
`tools/next-version` und `tools/pruefe-uebersetzungen.py` sind byteidentische Kopien der Vorlagen (`repo-vorlagen/vorlagen/abgleich.sh`).

README-Bilder: Karten aus `docs/readme-images.json`, gerendert mit `theme/v1/readme-images/render.mjs <repo> cards`
aus sh1ft-w.github.io. Das Titelbild `docs/images/readme-title.webp` ist eine fertige KI-Komposition (frei verwendbar,
ohne EXIF/XMP) und wird unverändert übernommen, für hell und dunkel dasselbe. Die Projektseite zeigt dasselbe Bild im Hero. `docs/images/stage-photo.jpg` =
Fotoausschnitt rechts daraus (ab x 760, ohne Text), nur noch im ungenutzten title-Block der readme-images.json.

## Harte Regeln
- **Nie zu einer echten Lampe verbinden, die gerade von einer anderen Home-Assistant-Instanz genutzt wird** (nur eine
  Bluetooth-Verbindung erlaubt). Tests nur gegen die Attrappe (`tests/conftest.py`).
