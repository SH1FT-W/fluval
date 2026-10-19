"""Konstanten der Fluval-Integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "fluvalble"
MANUFACTURER: Final = "Fluval"

# Schlüssel im Konfigurationseintrag – wie bei der alten Integration, damit der Eintrag weiterläuft.
CONF_MAC: Final = "mac"
CONF_MODEL: Final = "model"
CONF_PRODUCT_ID: Final = "product_id"
CONF_KEEP_CONNECTED: Final = "keep_connected"
CONF_WEATHER: Final = "weather_entity"
CONF_PREFERRED_PROXY: Final = "preferred_proxy"
PROXY_AUTO: Final = "auto"
DEFAULT_KEEP_CONNECTED: Final = True

STORAGE_VERSION: Final = 1

# Abfrage: verbunden alle 30 s (hält die Verbindung wach und erkennt hängende Verbindungen),
# sonst alle 5 Minuten kurz verbinden.
POLL_CONNECTED: Final = timedelta(seconds=30)
POLL_ON_DEMAND: Final = timedelta(minutes=5)
# Nach so vielen Fehlversuchen hintereinander wird seltener versucht (bis höchstens alle 5 Minuten).
BACKOFF_AFTER: Final = 3
POLL_MAX: Final = timedelta(minutes=5)
# Ohne „Verbindung halten“: so lange nach dem letzten Befehl verbunden bleiben.
IDLE_DISCONNECT: Final = 30
# So lange gelten die letzten Werte noch, wenn die Lampe gerade nicht antwortet (danach „nicht verfügbar“).
STALE_CONNECTED: Final = timedelta(minutes=3)
STALE_ON_DEMAND: Final = timedelta(minutes=12)
# Ab so langer Funkstille gibt es einen Reparatur-Hinweis.
UNREACHABLE_ISSUE: Final = timedelta(minutes=30)

# Zustand halten: nach eigenem Befehl so lange abweichende Meldungen nicht korrigieren, höchstens so oft korrigieren.
SETTLE: Final = timedelta(seconds=5)
RESTORE_BACKOFF: Final = timedelta(seconds=60)
# Entitäten erst ab dieser RSSI-Änderung (dBm) neu schreiben, nicht bei jedem Werbepaket.
RSSI_STEP: Final = 5

# Übergänge: höchstens alle 2 s ein Schritt.
FADE_MIN_GAP: Final = 2.0

# Wochentage der Dienste (zeitgesteuerte Effekte): Kürzel wie in HA, Montag = 0.
WEEKDAYS: Final = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
