"""Bluetooth-Verbindung zur Lampe.

Die Lampe erlaubt genau eine Verbindung. Darum gilt hier:

- **Eine Warteschlange** (Lock): Verbinden, Schreiben und Lesen laufen strikt nacheinander, mit 0,2 s Abstand
  zwischen zwei Befehlen (wie die App).
- **Feste Zeitgrenzen**: Warten in der Schlange (`QUEUE_TIMEOUT`) und der Befehl selbst (`OPERATION_TIMEOUT`) sind
  begrenzt – nichts kann minutenlang hängen. Läuft eine Grenze ab, wird die Verbindung getrennt und neu aufgebaut.
- **Verbinden wird nie von außen abgebrochen**: Die Zeitgrenzen dafür hat `establish_connection` selbst (je Versuch).
  Ein abgebrochener Verbindungsaufbau über einen ESPHome-Proxy kann eine verwaiste Verbindung hinterlassen – die Lampe
  ist dann belegt, wirbt nicht mehr und bleibt unerreichbar, bis der Proxy neu startet.
- **Befehle erst, wenn verbunden**: Ist die Lampe getrennt, wird zuerst verbunden (über den Proxy mit freiem Platz
  und dem besten Signal, das sucht Home Assistant aus).
- **Bevorzugter Proxy** (Option): Der erste Verbindungsversuch geht über diesen Proxy. Sieht er die Lampe nicht, hat er
  keinen freien Platz oder scheitert er, übernimmt im selben Verbindungsaufbau die automatische Auswahl. Danach wird
  der bevorzugte Proxy `PREFERRED_RETRY` lang übersprungen und dann wieder versucht.
- Nach jedem Verbinden: Rückmeldungen einschalten, Uhr stellen (wie die App).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
from datetime import timedelta
import logging
import time
from typing import Any

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
import bleak_retry_connector
from bleak_retry_connector import establish_connection
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import protocol

_LOGGER = logging.getLogger(__name__)

WRITE_TIMEOUT = 10.0
STATUS_TIMEOUT = 4.0
CONFIRM_RETRY_TIMEOUT = 8.0  # zweite Abfrage nach einem Befehl (Programm/Modus speichern dauert länger)
QUEUE_TIMEOUT = 75.0  # Warten, bis ein anderer Vorgang (z. B. ein langsamer Verbindungsaufbau) fertig ist
OPERATION_TIMEOUT = 30.0  # der Befehl selbst, wenn verbunden (Schreiben + Bestätigung)
DISCONNECT_TIMEOUT = 5.0
COMMAND_GAP = 0.2
CHUNK_GAP = 0.005
CONNECT_ATTEMPTS = 3
# Lief eine Verbindung nicht über den bevorzugten Proxy: so lange warten, bis er wieder zuerst versucht wird.
PREFERRED_RETRY = timedelta(minutes=30)

BLE_ERRORS = (BleakError, EOFError, OSError)

# Einmal warnen, wenn die privaten HA-/bleak-retry-connector-Schnittstellen für den bevorzugten Proxy fehlen.
_warned_private_api = False


class FluvalError(Exception):
    """Fehler mit Übersetzungsschlüssel (für Meldungen in Home Assistant)."""

    key = "command_failed"


class NotInRange(FluvalError):
    key = "not_in_range"


class ConnectFailed(FluvalError):
    key = "connect_failed"


class NoAnswer(FluvalError):
    key = "no_answer"


class FluvalLink:
    """Hält die Verbindung und führt Befehle der Reihe nach aus."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        name: str,
        on_status: Callable[[dict[Any, Any]], None],
        on_change: Callable[[], None],
    ) -> None:
        self.hass = hass
        self.address = address
        self.name = name
        self._on_status = on_status
        self._on_change = on_change
        self._lock = asyncio.Lock()
        self._client: BleakClient | None = None
        self._write_char: BleakGATTCharacteristic | None = None
        self._response = False
        self._assembler = protocol.FrameAssembler()
        self._full = asyncio.Event()
        self._last_full: dict[Any, Any] | None = None
        self._last_write = 0.0
        self._stopped = False
        self.connected_since: Any = None
        self.source: str | None = None
        self.source_id: str | None = None  # Adresse des Proxys, über den die Verbindung läuft
        self.preferred: str | None = None  # Adresse des bevorzugten Proxys (None = automatisch)
        self.preferred_retry_at: Any = None
        self.last_error: str | None = None
        self.services: list[str] = []
        self.mtu: int | None = None
        self.stats = {"connects": 0, "disconnects": 0, "failures": 0, "commands": 0, "timeouts": 0}

    # --- Zustand --------------------------------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    @property
    def preferred_allowed(self) -> bool:
        return self.preferred is not None and (
            self.preferred_retry_at is None or dt_util.utcnow() >= self.preferred_retry_at
        )

    @property
    def should_return(self) -> bool:
        """Verbunden über einen anderen Proxy, und der bevorzugte darf wieder versucht werden."""
        return self.connected and self.source_id != self.preferred and self.preferred_allowed

    def reset_preferred(self) -> None:
        """Beim nächsten Verbinden sofort wieder den bevorzugten Proxy versuchen."""
        self.preferred_retry_at = None

    # --- öffentliche Vorgänge (alle über die Warteschlange) ------------------------------------------------

    async def status(self) -> dict[Any, Any]:
        """Verbinden (falls nötig) und den kompletten Zustand holen."""
        return await self._run(self._status)

    async def send(self, values: dict[int, Any]) -> dict[Any, Any]:
        """Befehl schicken und danach den kompletten Zustand als Bestätigung holen."""

        async def go() -> dict[Any, Any]:
            await self._write(protocol.command(values))
            self.stats["commands"] += 1
            try:
                return await self._status()
            except NoAnswer:
                # Nach Programmen oder einem Moduswechsel antwortet die Lampe manchmal erst nach ein paar Sekunden.
                return await self._status(patient=True)

        return await self._run(go)

    async def send_packet(self, packet: bytes) -> None:
        """Rohes Paket ohne Bestätigung (Uhr stellen, Lampe blinken)."""

        async def go() -> None:
            await self._write(packet)

        await self._run(go)

    async def disconnect(self) -> None:
        """Verbindung trennen (z. B. damit die Fluval-App verbinden kann)."""
        async with self._lock:
            await self._drop()

    async def stop(self) -> None:
        """Endgültig: nur beim Entladen des Eintrags aufrufen. Danach verbindet `_run` nicht mehr (`_stopped`),
        laufende Befehle enden mit Fehler; zum bloßen Trennen `disconnect()` nehmen."""
        self._stopped = True
        await self._drop()

    # --- intern ---------------------------------------------------------------------------------------------

    async def _run[T](self, job: Callable[[], Awaitable[T]]) -> T:
        try:
            async with asyncio.timeout(QUEUE_TIMEOUT):
                await self._lock.acquire()
        except TimeoutError as err:
            self.stats["timeouts"] += 1
            self._failed("Zeitüberschreitung in der Warteschlange")
            raise NoAnswer("Zeitüberschreitung") from err
        try:
            if self._stopped:
                raise FluvalError("gestoppt")
            if not self.connected:
                await self._connect()  # ohne äußere Zeitgrenze, siehe Modulbeschreibung
            async with asyncio.timeout(OPERATION_TIMEOUT):
                return await job()
        except FluvalError as err:
            self._failed(str(err))
            raise
        except TimeoutError as err:  # vor BLE_ERRORS: TimeoutError ist ein OSError
            # Etwas hing (Proxy, Lampe). Verbindung verwerfen, damit der nächste Versuch frisch anfängt.
            self.stats["timeouts"] += 1
            self._failed("Zeitüberschreitung")
            await self._drop()
            raise NoAnswer("Zeitüberschreitung") from err
        except BLE_ERRORS as err:
            self._failed(f"{type(err).__name__}: {err}")
            await self._drop()
            raise ConnectFailed(self.last_error) from err
        finally:
            self._lock.release()

    def _failed(self, message: str) -> None:
        self.stats["failures"] += 1
        self.last_error = message
        _LOGGER.debug("%s: %s", self.name, message)

    def _device(self) -> BLEDevice | None:
        return bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True)

    async def _connect(self) -> None:
        device = self._device()
        if device is None:
            raise NotInRange("Lampe von keinem Bluetooth-Proxy erreichbar")
        self._assembler.reset()
        use_preferred = self.preferred_allowed
        try:
            # Zeitgrenzen je Versuch setzt establish_connection selbst; hier nie von außen abbrechen.
            client = await establish_connection(
                _client_class(self.address, self.preferred if use_preferred else None),
                device,
                self.name,
                disconnected_callback=self._on_disconnected,
                max_attempts=CONNECT_ATTEMPTS,
                ble_device_callback=lambda: self._device() or device,
            )
        except (*BLE_ERRORS, TimeoutError) as err:
            if use_preferred:
                self._skip_preferred()
            raise ConnectFailed(f"Verbinden gescheitert: {type(err).__name__}: {err}") from err
        self._client = client
        self.source_id = _source_id(client, device)
        if use_preferred and self.source_id != self.preferred:
            self._skip_preferred()
        try:
            self.services = sorted(str(s.uuid) for s in client.services)
            char = client.services.get_characteristic(protocol.WRITE_UUID)
            if char is None or client.services.get_characteristic(protocol.NOTIFY_UUID) is None:
                raise ConnectFailed(f"Lampe hat den Fluval-Dienst FFF0 nicht (Dienste: {', '.join(self.services)})")
            self._write_char = char
            self._response = "write-without-response" not in char.properties
            self.mtu = getattr(client, "mtu_size", None)
            async with asyncio.timeout(WRITE_TIMEOUT):
                await client.start_notify(protocol.NOTIFY_UUID, self._on_notify)
            # Wie die App: direkt nach dem Verbinden die Uhr stellen.
            await self._write(protocol.clock_packet(dt_util.now()))
        except BaseException:
            await self._drop()
            raise
        self.source = _source_name(self.hass, client, device)
        self.connected_since = dt_util.utcnow()
        self.stats["connects"] += 1
        self.last_error = None
        _LOGGER.debug("%s verbunden über %s (MTU %s)", self.name, self.source, self.mtu)
        self._on_change()

    async def _write(self, packet: bytes) -> None:
        client, char = self._client, self._write_char
        if client is None or char is None or not client.is_connected:
            raise ConnectFailed("nicht verbunden")
        wait = self._last_write + COMMAND_GAP - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        size = max(20, (self.mtu or 23) - 3)
        _LOGGER.debug("%s ← %s", self.name, packet.hex(" "))
        for offset in range(0, len(packet), size):
            if offset:
                await asyncio.sleep(CHUNK_GAP)
            async with asyncio.timeout(WRITE_TIMEOUT):
                await client.write_gatt_char(char, packet[offset : offset + size], response=self._response)
        self._last_write = time.monotonic()

    async def _status(self, *, patient: bool = False) -> dict[Any, Any]:
        self._full.clear()
        self._last_full = None
        await self._write(protocol.READ_PARAMS)
        try:
            async with asyncio.timeout(CONFIRM_RETRY_TIMEOUT if patient else STATUS_TIMEOUT):
                await self._full.wait()
        except TimeoutError as err:
            raise NoAnswer("Lampe meldet ihren Zustand nicht") from err
        return self._last_full or {}

    def _skip_preferred(self) -> None:
        self.preferred_retry_at = dt_util.utcnow() + PREFERRED_RETRY
        _LOGGER.debug(
            "%s: bevorzugter Proxy %s nicht genutzt, wieder ab %s", self.name, self.preferred, self.preferred_retry_at
        )

    def _on_notify(self, _char: Any, data: bytearray) -> None:
        frame = self._assembler.feed(bytes(data))
        if frame is None:
            return
        _LOGGER.debug("%s → %s", self.name, frame)
        if protocol.is_full_status(frame):
            self._last_full = frame
            self._full.set()
        try:
            self._on_status(frame)
        except Exception:
            # Rückruf aus bleak: ein Fehler beim Verarbeiten darf die Verbindung nicht mitreißen.
            _LOGGER.exception("%s: Rückmeldung nicht verarbeitbar: %s", self.name, frame)

    def _on_disconnected(self, client: BleakClient) -> None:
        if client is not self._client:
            return
        self._forget()
        _LOGGER.debug("%s: Verbindung getrennt", self.name)
        self._on_change()

    def _forget(self) -> None:
        if self._client is not None:
            self.stats["disconnects"] += 1
        self._client = None
        self._write_char = None
        self.source_id = None
        self.connected_since = None
        self._assembler.reset()

    async def _drop(self) -> None:
        client = self._client
        self._forget()
        if client is not None:
            with contextlib.suppress(Exception):
                async with asyncio.timeout(DISCONNECT_TIMEOUT):
                    await client.disconnect()
            self._on_change()


def _source_name(hass: HomeAssistant, client: BleakClient, device: BLEDevice) -> str | None:
    """Name des Proxys/Adapters, über den die Verbindung tatsächlich läuft."""
    scanner = getattr(client, "_connected_scanner", None)
    if (name := getattr(scanner, "name", None)) is not None:
        return str(name)
    source = device.details.get("source") if isinstance(device.details, dict) else None
    if source is None:
        return None
    found = bluetooth.async_scanner_by_source(hass, str(source))
    return str(found.name) if found is not None else str(source)


def _source_id(client: BleakClient, device: BLEDevice) -> str | None:
    """Adresse des Proxys/Adapters, über den die Verbindung tatsächlich läuft."""
    scanner = getattr(client, "_connected_scanner", None)
    if (source := getattr(scanner, "source", None)) is not None:
        return str(source)
    source = device.details.get("source") if isinstance(device.details, dict) else None
    return str(source) if source is not None else None


def _client_class(address: str, preferred: str | None) -> type[BleakClient]:
    """Client-Klasse für `establish_connection`, die im ersten Versuch den bevorzugten Proxy nimmt.

    Home Assistant ersetzt beim Start die Bleak-Klassen durch eigene Wrapper, die den Proxy nach Signal, freien Plätzen
    und Fehlversuchen selbst auswählen (den Proxy des übergebenen Geräts beachten sie nicht). Darum wird die Klasse erst
    hier geholt und nur die Auswahl für den ersten Versuch ersetzt – Platzverwaltung und alles andere bleiben bei HA.
    """
    global _warned_private_api
    base: type[BleakClient] = bleak_retry_connector.BleakClientWithServiceCache
    if preferred is None:
        return base
    if not hasattr(base, "_async_get_best_available_backend_and_device"):
        # Private Schnittstelle (HA-Wrapper um bleak). Fehlt sie, hat sich HA intern geändert: nicht stumm auf
        # die automatische Wahl zurückfallen, sondern einmal sagen, dass der bevorzugte Proxy nicht wirkt.
        if not _warned_private_api:
            _warned_private_api = True
            _LOGGER.warning(
                "%s: bevorzugter Proxy nicht verfügbar, Home Assistant hat seine Bluetooth-Interna geändert "
                "(Methode _async_get_best_available_backend_and_device fehlt); die automatische Wahl übernimmt",
                address,
            )
        return base
    tries = 0

    class PreferredProxyClient(base):  # type: ignore[valid-type,misc]
        def _async_get_best_available_backend_and_device(self, manager: Any) -> Any:
            nonlocal tries
            tries += 1
            if tries == 1:
                for found in manager.async_scanner_devices_by_address(address, True):
                    if found.scanner.source != preferred:
                        continue
                    if backend := self._async_get_backend_for_ble_device(manager, found.scanner, found.ble_device):
                        _LOGGER.debug("%s: verbinde über den bevorzugten Proxy %s", address, found.scanner.name)
                        return backend
                    break
            return super()._async_get_best_available_backend_and_device(manager)

    return PreferredProxyClient
