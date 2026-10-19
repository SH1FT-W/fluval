"""Einrichtung über Bluetooth und Optionen."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.fluvalble.const import DOMAIN

from .conftest import MAC, FakeLamp, old_entry


def info(address: str = MAC, name: str = "ReefNano4.0_000001", manufacturer: dict | None = None):
    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData

    data = {65535: bytes.fromhex("ff0e020000000001022300")} if manufacturer is None else manufacturer
    adv = AdvertisementData(name, data, {}, ["00000200-0000-1000-8000-00805f9b34fb"], None, -64, ())
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=-64,
        manufacturer_data=data,
        service_data={},
        service_uuids=adv.service_uuids,
        source="local",
        device=BLEDevice(address, name, {}),
        advertisement=adv,
        connectable=True,
        time=0,
        tx_power=None,
    )


async def test_bluetooth_discovery(hass: HomeAssistant, lamp: FakeLamp):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=info())
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "bluetooth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "ReefNano4.0_000001"
    assert result["data"]["mac"] == MAC and result["data"]["product_id"] == 547
    assert result["result"].unique_id == "02:00:00:00:00:01"
    assert result["options"] == {"keep_connected": True}
    await hass.async_block_till_done()
    # zweites Mal: schon eingerichtet
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=info())
    assert result["reason"] == "already_configured"


async def test_bluetooth_other_device(hass: HomeAssistant):
    other = info(name="Thermometer", manufacturer={65535: bytes(12)})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=other)
    assert result["reason"] == "not_supported"


async def test_user_picks_lamp(hass: HomeAssistant, lamp: FakeLamp):
    found = [info(), info("AA:BB:CC:DD:EE:FF", "Other", {76: b"\x01"})]
    with patch("custom_components.fluvalble.config_flow.bluetooth.async_discovered_service_info", return_value=found):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"mac": MAC})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


async def test_user_nothing_found(hass: HomeAssistant):
    with patch("custom_components.fluvalble.config_flow.bluetooth.async_discovered_service_info", return_value=[]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["reason"] == "no_devices_found"


async def test_options(hass: HomeAssistant, lamp: FakeLamp):
    entry = old_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"keep_connected": False})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options == {"keep_connected": False, "preferred_proxy": "auto"}
    assert entry.runtime_data.keep_connected is False
    await hass.config_entries.async_unload(entry.entry_id)
