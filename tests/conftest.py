"""Nachgebaute Fluval Reef Nano 4.0 – antwortet wie die echte Lampe laut Fluval-Connect-App.

Die Lampe spricht CBOR über die Bluetooth-Merkmale FFF2 (Befehle) und FFF1 (Rückmeldungen). Diese Attrappe
setzt Befehle aus mehreren Paketen zusammen, schickt den Zustand in 20-Byte-Stücken zurück (wie bei kleiner
MTU über einen Proxy) und kann typische Störungen nachstellen: nicht in Reichweite, Verbindung bricht ab,
antwortet nicht, späte Rückmeldung eines alten Werts, eigene Szene nach dem Einschalten.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from unittest.mock import patch

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fluvalble import protocol
from custom_components.fluvalble.const import DOMAIN

ENTRY_ID = "01M1YAPACSFFNX2WYXT22R8VG0"
MAC = "02:00:00:00:00:01"
UID = "020000000001"


class FakeChar:
    def __init__(self, uuid: str, properties: list[str]) -> None:
        self.uuid = uuid
        self.properties = properties


class FakeServices:
    def __init__(self, lamp: FakeLamp) -> None:
        self.lamp = lamp
        self.chars = {
            protocol.WRITE_UUID: FakeChar(protocol.WRITE_UUID, ["write", "write-without-response"]),
            protocol.NOTIFY_UUID: FakeChar(protocol.NOTIFY_UUID, ["notify"]),
        }
        if lamp.without_fff0:
            self.chars = {}

    def get_characteristic(self, uuid: str) -> FakeChar | None:
        return self.chars.get(uuid)

    def __iter__(self):
        uuids = [protocol.SERVICE_UUID] if self.chars else ["00001800-0000-1000-8000-00805f9b34fb"]
        return iter([type("S", (), {"uuid": u})() for u in uuids])


class FakeClient:
    """Wie ein verbundener BleakClient (über einen ESPHome-Proxy)."""

    def __init__(self, lamp: FakeLamp, disconnected_callback: Callable[[Any], None]) -> None:
        self.lamp = lamp
        self._cb = disconnected_callback
        self.is_connected = True
        self.services = FakeServices(lamp)
        self.mtu_size = 23
        self._notify: Callable[[Any, bytearray], None] | None = None
        self._buffer = b""
        self._connected_scanner = type("Scanner", (), {"name": lamp.via_name, "source": lamp.via})()

    async def start_notify(self, uuid: str, callback: Callable[[Any, bytearray], None]) -> None:
        self._notify = callback

    async def write_gatt_char(self, char: FakeChar, data: bytes, response: bool = False) -> None:
        if self.lamp.hang:
            await asyncio.sleep(3600)
        if not self.is_connected:
            raise BleakError("Not connected")
        if self.lamp.drop_on_write:
            self.lamp.drop_on_write = False
            self.drop()
            raise BleakError("Disconnected during write")
        self.lamp.writes.append(bytes(data))
        self._buffer += bytes(data)
        packet = self._buffer
        if packet[:1] == bytes((protocol.CLOCK_OPCODE,)):
            if len(packet) < 8:
                return
            self._buffer = b""
            self.lamp.clock.append(packet)
            return
        if packet == protocol.READ_PARAMS:
            self._buffer = b""
            if self.lamp.late:
                self.lamp.late -= 1
            elif not self.lamp.silent:
                self.notify(protocol.STATUS_HEADER, self.lamp.full_status())
            return
        if packet[:1] == bytes((protocol.COMMAND_HEADER,)):
            try:
                values, end = protocol.cbor_decode(packet, 1)
            except protocol.ProtocolError:
                return  # weitere Stücke folgen
            self._buffer = b""
            assert end == len(packet)
            echo = self.lamp.apply(values)
            if echo:
                self.notify(protocol.COMMAND_HEADER, echo)
            return
        self._buffer = b""
        raise AssertionError(f"unbekanntes Paket {packet.hex()}")

    def notify(self, header: int, values: dict[int, Any]) -> None:
        """Rückmeldung in 20-Byte-Stücken (wie bei kleiner MTU)."""
        frame = bytes((header,)) + protocol.cbor_encode(values)
        for offset in range(0, len(frame), 20):
            assert self._notify is not None
            self._notify(None, bytearray(frame[offset : offset + 20]))

    def drop(self) -> None:
        self.is_connected = False
        self.lamp.client = None
        self._cb(self)

    async def disconnect(self) -> None:
        if self.is_connected:
            self.drop()


class FakeLamp:
    def __init__(self) -> None:
        self.mode = 0
        self.power = True
        self.via = "02:00:00:00:00:A1"  # Proxy, über den die Attrappe „verbunden“ wird
        self.via_name = "esp32-bluetooth-proxy-0000a1"
        self.client_class: Any = None
        self.channels = [22, 77, 100, 66, 44]  # Pink, Cyan, Blau, Violett, Kaltweiß (Beispiel-Tageswerte)
        self.effect = 0
        self.firmware = 14
        self.sunrise = bytes((7, 0, 60))
        self.sunset = bytes((17, 0, 60))
        self.sleep = bytes((22, 0))
        self.day = bytes((50, 50, 100, 50, 30))
        self.night = bytes((0, 0, 5, 5, 0))
        self.pro = bytes(
            (4, 9, 0, 0, 0, 0, 0, 0, 10, 0, 22, 77, 100, 66, 44, 20, 0, 25, 0, 25, 10, 0, 22, 0, 0, 0, 0, 0, 0)
        )
        # Ein aktives Zeitfenster: Mo–Fr 12:00–12:30 „Blitze“ (Bit 0 = Sonntag)
        self.effect_schedule = bytes((0x80 | 0b0111110, 12, 0, 12, 30, 1)) + bytes(36)
        self.in_range = True
        self.offline = False  # Verbinden scheitert
        self.silent = False  # beantwortet „Zustand holen“ nicht
        self.late = 0  # so viele Abfragen bleiben unbeantwortet (Lampe noch mit Speichern beschäftigt)
        self.ignore = 0  # so viele Befehle werden angenommen, aber nicht umgesetzt
        self.hang = False
        self.connect_delay = 0.0  # langsamer Verbindungsaufbau über den Proxy
        self.drop_on_write = False
        self.without_fff0 = False
        self.scene_on_power = None  # Werte, die die Lampe beim Einschalten selbst setzt
        self.client: FakeClient | None = None
        self.connects = 0
        self.writes: list[bytes] = []
        self.commands: list[dict[int, Any]] = []
        self.clock: list[bytes] = []

    def full_status(self) -> dict[int, Any]:
        return {
            0: self.firmware,
            1: self.mode,
            2: self.power,
            **dict(zip(protocol.KEY_CHANNELS, self.channels, strict=True)),
            8: self.sunrise,
            9: self.sunset,
            10: self.sleep,
            11: self.day,
            12: self.night,
            13: self.pro,
            14: self.effect,
            15: self.effect_schedule,
        }

    def apply(self, values: dict[int, Any]) -> dict[int, Any]:
        self.commands.append(values)
        if self.ignore:
            self.ignore -= 1
            return {}
        if 1 in values:
            self.mode = values[1]
        if 2 in values:
            if values[2] and not self.power and self.scene_on_power is not None:
                self.channels = list(self.scene_on_power)
            self.power = values[2]
        for i, key in enumerate(protocol.KEY_CHANNELS):
            if key in values:
                self.channels[i] = values[key]
        if 14 in values:
            self.effect = values[14]
        if 15 in values:
            self.effect_schedule = values[15]
        if 13 in values:
            self.pro = values[13]
        return {k: v for k, v in values.items() if k != protocol.KEY_FIND}

    def push(self, values: dict[int, Any]) -> None:
        """Lampe meldet sich von selbst (z. B. spätes Echo)."""
        for i, key in enumerate(protocol.KEY_CHANNELS):
            if key in values:
                self.channels[i] = values[key]
        if 2 in values:
            self.power = values[2]
        if 1 in values:
            self.mode = values[1]
        assert self.client is not None
        self.client.notify(protocol.STATUS_HEADER, self.full_status())

    async def connect(
        self, _cls: Any, device: BLEDevice, name: str, disconnected_callback: Any, **kw: Any
    ) -> FakeClient:
        if self.hang:
            await asyncio.sleep(3600)
        if self.connect_delay:
            await asyncio.sleep(self.connect_delay)
        if self.offline:
            raise BleakError("BleakOutOfConnectionSlotsError: no free slot")
        self.connects += 1
        self.client_class = _cls
        self.client = FakeClient(self, disconnected_callback)
        return self.client


@pytest.fixture(autouse=True)
def auto_enable(enable_custom_integrations, mock_bluetooth, hass: HomeAssistant):
    hass.config.language = "de"


@pytest.fixture(autouse=True)
def fast():
    """Pausen kosten im Test nur Zeit."""
    with (
        patch("custom_components.fluvalble.ble.COMMAND_GAP", 0),
        patch("custom_components.fluvalble.ble.CHUNK_GAP", 0),
        patch("custom_components.fluvalble.ble.STATUS_TIMEOUT", 0.05),
        patch("custom_components.fluvalble.ble.CONFIRM_RETRY_TIMEOUT", 0.05),
    ):
        yield


@pytest.fixture
def lamp():
    fake = FakeLamp()
    device = BLEDevice(MAC, "ReefNano4.0_000001", {"source": "02:00:00:00:00:A1"})
    with (
        patch("custom_components.fluvalble.ble.establish_connection", side_effect=fake.connect),
        patch(
            "custom_components.fluvalble.ble.FluvalLink._device",
            side_effect=lambda: device if fake.in_range else None,
            autospec=False,
        ),
    ):
        yield fake


def old_entry(**options: Any) -> MockConfigEntry:
    """So liegt der Eintrag der alten Integration in einer bestehenden Installation."""
    return MockConfigEntry(
        domain=DOMAIN,
        entry_id=ENTRY_ID,
        title="ReefNano4.0_000001",
        version=2,
        minor_version=1,
        unique_id="02:00:00:00:00:01",
        data={
            "mac": MAC,
            "manufacturer_data": {"65535": "ff0e020000000001022300"},
            "model": "Fluval Reef Nano 4.0 LED",
            "product_id": 547,
            "service_data": {},
            "service_uuids": ["00000200-0000-1000-8000-00805f9b34fb"],
        },
        options=options
        or {"active_time": 0, "lamp_profile": "auto", "ping_interval": 10, "restore_previous_mode": False},
    )


@pytest.fixture
async def setup(hass: HomeAssistant, lamp: FakeLamp):
    entry = old_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry
    if entry.state.recoverable:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
