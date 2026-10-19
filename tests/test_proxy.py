"""Bevorzugter Proxy: erst der gewählte, sonst automatisch, später wieder der gewählte."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fluvalble import ble
from custom_components.fluvalble.ble import PREFERRED_RETRY, _client_class

from .conftest import MAC, FakeLamp, old_entry
from .test_integration import tick

PROXY_A = "02:00:00:00:00:A1"
PROXY_B = "02:00:00:00:00:B2"


class Scanner:
    def __init__(self, source: str, name: str, *, free: bool = True, connectable: bool = True) -> None:
        self.source, self.name, self.free, self.connectable = source, name, free, connectable


class Found:
    def __init__(self, scanner: Scanner, rssi: int) -> None:
        self.scanner = scanner
        self.ble_device = f"device via {scanner.name}"
        self.advertisement = type("Adv", (), {"rssi": rssi})()


class Manager:
    def __init__(self, *found: Found) -> None:
        self.found = list(found)

    def async_scanner_devices_by_address(self, address: str, connectable: bool) -> list[Found]:
        assert address == MAC and connectable
        return self.found


class HaClient:
    """Wie Home Assistants Wrapper: sucht sich den Proxy selbst (hier: immer „auto“)."""

    def _async_get_backend_for_ble_device(self, _manager: Any, scanner: Scanner, device: str) -> str | None:
        return f"backend {scanner.name}" if scanner.free else None

    def _async_get_best_available_backend_and_device(self, _manager: Any) -> str:
        return "auto"


@pytest.fixture
def ha_client():
    with patch("custom_components.fluvalble.ble.bleak_retry_connector.BleakClientWithServiceCache", HaClient):
        yield


def test_preferred_first_then_automatic(ha_client):
    manager = Manager(Found(Scanner(PROXY_B, "esp32-proxy-b"), -60), Found(Scanner(PROXY_A, "esp32-proxy-a"), -80))
    client = _client_class(MAC, PROXY_A)()
    assert client._async_get_best_available_backend_and_device(manager) == "backend esp32-proxy-a"
    # zweiter Versuch im selben Verbindungsaufbau: Home Assistant wählt
    assert client._async_get_best_available_backend_and_device(manager) == "auto"


def test_preferred_without_slot_or_not_seen(ha_client):
    full = Manager(Found(Scanner(PROXY_A, "esp32-proxy-a", free=False), -50))
    assert _client_class(MAC, PROXY_A)()._async_get_best_available_backend_and_device(full) == "auto"
    unseen = Manager(Found(Scanner(PROXY_B, "esp32-proxy-b"), -60))
    assert _client_class(MAC, PROXY_A)()._async_get_best_available_backend_and_device(unseen) == "auto"


def test_automatic_uses_plain_class(ha_client):
    assert _client_class(MAC, None) is HaClient


def test_missing_private_api_warns_once(ha_client):
    """Fehlt HAs private Proxy-Auswahl (Interna geändert), gibt es einmal eine Warnung statt stummem Umschalten."""

    class PlainClient:  # reines bleak ohne Home-Assistant-Wrapper
        pass

    ble._warned_private_api = False
    with (
        patch("custom_components.fluvalble.ble.bleak_retry_connector.BleakClientWithServiceCache", PlainClient),
        patch.object(ble._LOGGER, "warning") as warning,
    ):
        assert _client_class(MAC, PROXY_A) is PlainClient
        assert _client_class(MAC, PROXY_A) is PlainClient
        assert _client_class(MAC, None) is PlainClient  # ohne Wunsch-Proxy nichts zu melden
    assert warning.call_count == 1 and "bevorzugter Proxy nicht verfügbar" in warning.call_args.args[0]
    ble._warned_private_api = False


class _Device:
    def __init__(self, details: Any) -> None:
        self.details = details


def test_source_name_and_id_fallbacks(hass: HomeAssistant):
    """Ohne `_connected_scanner` (anderer Backend-Typ) zählt die Quelle aus den Gerätedetails."""
    found = type("Scanner", (), {"name": "esp32-proxy-a"})()
    with patch("custom_components.fluvalble.ble.bluetooth.async_scanner_by_source", return_value=found):
        assert ble._source_name(hass, object(), _Device({"source": PROXY_A})) == "esp32-proxy-a"
    with patch("custom_components.fluvalble.ble.bluetooth.async_scanner_by_source", return_value=None):
        assert ble._source_name(hass, object(), _Device({"source": PROXY_A})) == PROXY_A  # Adresse statt Name
    assert ble._source_name(hass, object(), _Device({})) is None
    assert ble._source_name(hass, object(), _Device(None)) is None  # details kein dict (lokaler Adapter)
    assert ble._source_id(object(), _Device({"source": PROXY_B})) == PROXY_B
    assert ble._source_id(object(), _Device(None)) is None
    scanner = type("Scanner", (), {"name": "esp32-proxy-b", "source": PROXY_B})()
    client = type("Client", (), {"_connected_scanner": scanner})()
    assert ble._source_name(hass, client, _Device(None)) == "esp32-proxy-b"
    assert ble._source_id(client, _Device(None)) == PROXY_B


async def setup_with(hass: HomeAssistant, **options: Any):
    old = old_entry()
    entry = MockConfigEntry(
        domain=old.domain,
        entry_id=old.entry_id,
        title=old.title,
        version=2,
        minor_version=2,
        unique_id=old.unique_id,
        data=dict(old.data),
        options={"keep_connected": True, **options},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_connect_via_preferred(hass: HomeAssistant, lamp: FakeLamp):
    entry = await setup_with(hass, preferred_proxy=PROXY_A.lower())
    link = entry.runtime_data.link
    assert link.preferred == PROXY_A
    assert link.connected and link.source_id == PROXY_A
    assert lamp.client_class is not None
    assert link.preferred_retry_at is None and not link.should_return
    await hass.config_entries.async_unload(entry.entry_id)


async def test_fallback_then_back_to_preferred(hass: HomeAssistant, lamp: FakeLamp, freezer: FrozenDateTimeFactory):
    lamp.via, lamp.via_name = PROXY_B, "esp32-proxy-b"
    entry = await setup_with(hass, preferred_proxy=PROXY_A)
    c = entry.runtime_data
    link = c.link
    # lief über Proxy B → Proxy A erst in 30 Minuten wieder versuchen
    assert link.connected and link.source_id == PROXY_B
    assert link.preferred_retry_at is not None and not link.should_return
    diag = await ble_diag(hass, entry)
    assert diag["preferred_proxy"] == PROXY_A and diag["source_id"] == PROXY_B

    lamp.via, lamp.via_name = PROXY_A, "esp32-proxy-a"
    freezer.tick(PREFERRED_RETRY)
    assert link.should_return
    connects = lamp.connects
    await tick(hass, freezer)
    assert lamp.connects == connects + 1
    assert link.source_id == PROXY_A and not link.should_return
    assert any("zurück zum bevorzugten Proxy" in line for line in c.stats.log)
    await hass.config_entries.async_unload(entry.entry_id)


async def test_failed_connect_skips_preferred(hass: HomeAssistant, lamp: FakeLamp):
    lamp.offline = True
    entry = await setup_with(hass, preferred_proxy=PROXY_A)
    link = entry.runtime_data.link
    assert not link.connected and link.preferred_retry_at is not None
    assert not link.preferred_allowed
    lamp.offline = False
    # „Neu verbinden“ versucht sofort wieder den bevorzugten Proxy
    await entry.runtime_data.async_reconnect()
    assert link.connected and link.source_id == PROXY_A and link.preferred_retry_at is None
    await hass.config_entries.async_unload(entry.entry_id)


async def test_options_list_proxies(hass: HomeAssistant, lamp: FakeLamp):
    entry = await setup_with(hass, preferred_proxy="AA:AA:AA:AA:AA:AA")
    scanners = [
        Scanner(PROXY_A, "esp32-proxy-a"),
        Scanner(PROXY_B, "esp32-proxy-b"),
        Scanner("11:22:33:44:55:66", "shelly", connectable=False),
    ]
    with (
        patch("custom_components.fluvalble.config_flow.bluetooth.async_current_scanners", return_value=scanners),
        patch(
            "custom_components.fluvalble.config_flow.bluetooth.async_scanner_devices_by_address",
            return_value=[Found(scanners[0], -71)],
        ),
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    selector = result["data_schema"].schema["preferred_proxy"]
    options = {o["value"]: o["label"] for o in selector.config["options"]}
    assert options == {
        "auto": "Automatisch (bestes Signal)",
        PROXY_B: "esp32-proxy-b",
        PROXY_A: "esp32-proxy-a (-71 dBm)",
        "AA:AA:AA:AA:AA:AA": "AA:AA:AA:AA:AA:AA (gerade nicht da)",
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"keep_connected": True, "preferred_proxy": PROXY_A}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.runtime_data.link.preferred == PROXY_A
    await hass.config_entries.async_unload(entry.entry_id)


async def ble_diag(hass: HomeAssistant, entry) -> dict[str, Any]:
    from custom_components.fluvalble.diagnostics import async_get_config_entry_diagnostics

    return (await async_get_config_entry_diagnostics(hass, entry))["connection"]


def test_retry_constant():
    assert timedelta(minutes=30) == PREFERRED_RETRY
    assert ble.PREFERRED_RETRY is PREFERRED_RETRY


async def test_options_labels_english(hass: HomeAssistant, lamp: FakeLamp):
    hass.config.language = "en"
    entry = await setup_with(hass, preferred_proxy="AA:AA:AA:AA:AA:AA")
    with patch("custom_components.fluvalble.config_flow.bluetooth.async_current_scanners", return_value=[]):
        result = await hass.config_entries.options.async_init(entry.entry_id)
    options = {o["value"]: o["label"] for o in result["data_schema"].schema["preferred_proxy"].config["options"]}
    assert options == {"auto": "Automatic (best signal)", "AA:AA:AA:AA:AA:AA": "AA:AA:AA:AA:AA:AA (not seen right now)"}
    await hass.config_entries.async_unload(entry.entry_id)
