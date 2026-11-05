# Fluval – Projektregeln

HA-Integration (`custom_components/fluvalble`, Domain `fluvalble`) für Fluval-Lampen der aktuellen Bluetooth-Generation
(Dienst FFF0, CBOR, 5 Kanäle; Modelle in `products.py`). Entwickelt und geprüft an einer Reef Nano 4.0 (Produkt 547);
die übrigen Modelle sind nach App-Daten umgesetzt, aber ohne Hardware geprüft. Nicht unterstützt: alte verschlüsselte
Generation, AquaSky 3.0 (532, FACEBD), Roma & Shaker 2.0 (564, RGBW).
Nachfolger von MrMooreUK/fluvalble **mit gleicher Domain und gleichen unique_ids** – Einbau = Ordner tauschen,
Entity-IDs, Verlauf, Gerät, Automationen und Skripte bleiben. Für 547 darf sich bei Erweiterungen nichts ändern
(unique_ids, Entity-IDs, Kanalnamen, Verhalten). Übernommene Teile (Produkttabelle, Spektren, Farbumrechnung) in `NOTICE`.

- Repo ist **öffentlich**: nichts Persönliches ins Repo (keine Namen, echten MACs/IPs, Proxy-Namen, Heimnetz-Details,
  lokalen Pfade). Testdaten bleiben fiktiv (MAC 02:00:00:00:00:01, Proxys 02:00:00:00:00:A1/B2).
- README, CHANGELOG, Release-Notizen, Issue-Texte und Commit-Texte auf Englisch.
- Lokal committen ok, Push und Releases nur nach Rückfrage.
- **Nie zu einer echten Lampe verbinden, die gerade von einer anderen Home-Assistant-Instanz genutzt wird** (nur eine
  Bluetooth-Verbindung erlaubt). Tests nur gegen die Attrappe (`tests/conftest.py`).

## Aufbau (`custom_components/fluvalble/`)
- `protocol.py` rein: Rahmen, CBOR-Schlüssel, `FrameAssembler`. Quelle: FluvalConnect-APK
  (`eu/hagen/fluvalconnect/kxt/LightKxtKt.java`).
- `ble.py` Verbindung (eine Warteschlange, feste Zeitgrenzen, bevorzugter Proxy).
- `coordinator.py` Abfrage, Verfügbarkeit, `desired` (Store) = was HA zuletzt wollte → wiederherstellen, Befehle mit
  Bestätigung.
- `products.py` Produkttabelle; `light.py` RGB über Spektraldaten (`color.py`); `profiles.py` gespeicherte
  Profi-Programme in der Option `profiles`.
- unique_ids `<MAC ohne Doppelpunkte>_<key>`, nicht ändern. Neue Entity-IDs übernehmen das Präfix des Lichts
  (`_object_prefix` in `__init__.py`). Eintrag Version 2.2.

## Warum es so ist (steht nicht im Code)
- Modus/an/Kanäle als **getrennte** Frames wie die App; kleinere CBOR-Maps sind Echos, kein Zustand.
- Effekt-Zeitfenster: Bit 0 = **Sonntag** (Uhr dagegen WD 1 = Montag).
- Profile: Die App hält ihre Profile auf dem Handy, die Lampe nur das aktive Programm (Schlüssel 13) → nicht
  auslesbar, darum eigene Liste in HA. Speichern dekodiert Schlüssel 13 aus dem Rückgabewert von `link.status()`
  (nicht `state`, das behält Altes) und liest die Liste erst nach dem letzten `await`. Nicht lesbare Einträge bleiben
  unverändert erhalten.
- Logo: `brand/` aus der offiziellen SVG von fluvalaquatics.com (`assets/fluval-logo.svg`), mit chrome-headless-shell
  gerendert (Icon = nur das runde Zeichen).

## Version
Versionen über `tools/next-version` (Regeln in der globalen CLAUDE.md); Tag + Release-Titel = genau die
manifest-Version. `CHANGELOG.md` `## YY.M.N – DD.MM.YYYY`, englisch. Die Versions-Badge im README und die
Versionszeile der Projektseite (`docs/index.html`) lesen die manifest.json selbst.

## Prüfen vor „fertig“
- Eigene venv (HA 2026.9.3 + Bluetooth-Pakete, dieselben Versionen wie die CI): `uv venv --python 3.14 .venv` und
  `uv pip install --python .venv/bin/python -r requirements_test.txt`.
- `LC_ALL=en_US.UTF-8 TZ=UTC .venv/bin/python -m pytest -q -p no:logging --cov=custom_components/fluvalble` (≥ 96 %),
  `uvx ruff@0.16.9 check . && uvx ruff@0.16.9 format --check .`, `uvx mypy@2.3.1 --python-executable .venv/bin/python`,
  `.venv/bin/python tools/pruefe-uebersetzungen.py`,
  hassfest: `docker run --rm -v "$PWD/custom_components:/github/workspace/custom_components" ghcr.io/home-assistant/hassfest`.
- Tests mit `freezer` frieren auch die Loop-Zeit ein → Zeitgrenzen (`asyncio.timeout`) laufen dort nie ab;
  Zeitüberschreitungen ohne `freezer` testen.
- CI: `.github/workflows/pruefung.yml` (Vorlage repo-vorlagen/vorlagen/ci-ha-integration.yml): dasselbe mit
  Abdeckungsgrenze 96 %, plus hassfest, montags Frühwarnung gegen die neueste HA.
- `tools/next-version` und `tools/pruefe-uebersetzungen.py` sind byteidentische Kopien der Vorlagen
  (`repo-vorlagen/vorlagen/abgleich.sh`).
- README-Bilder aus `docs/readme-images.json` mit `theme/v1/readme-images/render.mjs <repo>` aus sh1ft-w.github.io:
  Titel `docs/images/readme-title-{light,dark}.webp` (2560 × 1280, Fenster zeigt `docs/images/title-shot-*.png` =
  Funktionsblock der Projektseite, 2× aufgenommen) plus `readme-title-social.png` für die GitHub-Vorschau; README-Aufbau nach `repo-vorlagen/readme-muster.md`, ohne Kartentabellen. Die Projektseite zeigt das
  Titelbild im Hero.
