"""Other lights of the current generation: product table, discovery, a Plant 4.0 against the fake lamp."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_BLUETOOTH
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fluvalble.color import levels_to_rgb, rgb_to_levels
from custom_components.fluvalble.const import DOMAIN
from custom_components.fluvalble.diagnostics import async_get_config_entry_diagnostics
from custom_components.fluvalble.products import (
    ALL_CHANNELS,
    PLANT_XYZ,
    PRODUCTS,
    REEF_XYZ,
    product_for,
    product_id_from_advertisement,
)

from .conftest import MAC, UID, FakeLamp
from .test_config_flow import info

PLANT_AD = "ff0e" + MAC.replace(":", "").lower() + "022100"  # product 0x0221 = 545


def test_table():
    # Exactly the five-channel FFF0 lights; old generation, AquaSky 3.0 (FACEBD) and RGBW 564 are not listed.
    assert sorted(PRODUCTS) == [385, 386, 545, 546, 547, 548, 563]
    assert PRODUCTS[547].model == "Fluval Reef Nano 4.0 LED"
    assert PRODUCTS[547].channels == ("pink", "cyan", "blue", "purple", "cold_white")
    assert PRODUCTS[548].channels == ("pink", "blue", "cold_white", "pure_white", "warm_white")
    for product in PRODUCTS.values():
        assert product.default_on[product.blue] == 20 and sum(product.default_on) == 20
    assert ALL_CHANNELS == ("pink", "cyan", "blue", "purple", "cold_white", "pure_white", "warm_white")
    assert product_for(532) is None and product_for(True) is None and product_for("547") is None


def test_advertisement_product_id():
    payload = bytes.fromhex(PLANT_AD)
    assert product_id_from_advertisement({0xFFFF: payload}) == 545
    assert product_id_from_advertisement({0x01FF: payload}) == 545  # raw company bytes FF 01
    assert product_id_from_advertisement({0x004C: payload}) is None
    assert product_id_from_advertisement({0xFFFF: payload[:9]}) is None


def test_colour_per_model():
    # Reef default stays exactly as before; the Plant spectrum mixes differently.
    assert rgb_to_levels((0, 0, 255), 255) == rgb_to_levels((0, 0, 255), 255, REEF_XYZ)
    assert rgb_to_levels((255, 255, 255), 255, PLANT_XYZ) != rgb_to_levels((255, 255, 255), 255, REEF_XYZ)
    assert levels_to_rgb((0, 0, 0, 0, 100), PLANT_XYZ) != levels_to_rgb((0, 0, 0, 0, 100), REEF_XYZ)


async def test_discovery_plant(hass: HomeAssistant, lamp: FakeLamp):
    data = info(name="Plant4.0_000001", manufacturer={65535: bytes.fromhex(PLANT_AD)})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=data)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["product_id"] == 545 and result["data"]["model"] == "Fluval Plant 4.0 LED"
    await hass.async_block_till_done()


async def test_discovery_unsupported_products(hass: HomeAssistant):
    for product in ("0214", "0234", "0122"):  # AquaSky 3.0, Roma & Shaker 2.0, old Marine & Reef
        ad = {65535: bytes.fromhex("ff0e" + "00" * 6 + product + "00")}
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=info(name="ReefNano4.0_X", manufacturer=ad)
        )
        assert result["reason"] == "not_supported"


async def test_user_flow_unnamed_plant(hass: HomeAssistant, lamp: FakeLamp, monkeypatch: pytest.MonkeyPatch):
    found = info(name="", manufacturer={511: bytes.fromhex(PLANT_AD)})
    monkeypatch.setattr(
        "custom_components.fluvalble.config_flow.bluetooth.async_discovered_service_info", lambda *a, **k: [found]
    )
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["data_schema"].schema["mac"].container == {MAC: f"Fluval Plant 4.0 LED ({MAC})"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"mac": MAC})
    assert result["title"] == "Fluval Plant 4.0 LED" and result["data"]["product_id"] == 545
    await hass.async_block_till_done()


def plant_entry(product_id: int | None = 545) -> MockConfigEntry:
    data = {"mac": MAC, "model": "Fluval Plant 4.0 LED", "manufacturer_data": {"65535": PLANT_AD}}
    if product_id is not None:
        data["product_id"] = product_id
    return MockConfigEntry(
        domain=DOMAIN,
        title="Plant",
        version=2,
        minor_version=2,
        unique_id=MAC.lower(),
        data=data,
        options={"keep_connected": True},
    )


@pytest.fixture
async def plant(hass: HomeAssistant, lamp: FakeLamp):
    lamp.channels = [10, 20, 30, 40, 50]  # Pink, Blue, Cold white, Pure white, Warm white
    entry = plant_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_plant_entities(hass: HomeAssistant, plant: MockConfigEntry, lamp: FakeLamp):
    reg = er.async_get(hass)
    names = ("pink", "blue", "cold_white", "pure_white", "warm_white")
    for index, name in enumerate(names, start=1):
        entity_id = reg.async_get_entity_id("number", DOMAIN, f"{UID}_channel_{index}")
        assert entity_id == f"number.plant_{name}"
        assert hass.states.get(entity_id).state == str(10 * index)
    assert hass.states.get("number.plant_warm_white").attributes["friendly_name"] == "Plant Warmweiß"
    assert hass.states.get("light.plant_light").attributes["channels"] == dict(
        zip(names, (10, 20, 30, 40, 50), strict=True)
    )
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, MAC), plant.entry_id)
    assert device is not None and device.model == "Fluval Plant 4.0 LED"
    diag = await async_get_config_entry_diagnostics(hass, plant)
    assert diag["product"]["id"] == 545


async def test_plant_commands(hass: HomeAssistant, plant: MockConfigEntry, lamp: FakeLamp):
    await hass.services.async_call(
        DOMAIN, "set_channels", {"entity_id": "light.plant_light", "warm_white": 77}, blocking=True
    )
    assert lamp.channels == [10, 20, 30, 40, 77]
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "set_channels", {"entity_id": "light.plant_light", "cyan": 5}, blocking=True
        )
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.plant_pure_white", "value": 3}, blocking=True
    )
    assert lamp.channels[3] == 3
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.plant_light", "rgb_color": [255, 180, 120], "brightness": 255},
        blocking=True,
    )
    assert lamp.channels == list(rgb_to_levels((255, 180, 120), 255, PLANT_XYZ))
    await hass.services.async_call(
        DOMAIN,
        "set_pro_program",
        {"entity_id": "light.plant_light", "points": [{"time": "09:00", "warm_white": 60}], "activate": False},
        blocking=True,
    )
    programs = await hass.services.async_call(
        DOMAIN, "get_programs", {"entity_id": "light.plant_light"}, blocking=True, return_response=True
    )
    result = programs["light.plant_light"]
    assert result["professional"] == [
        {"time": "09:00", "pink": 0, "blue": 0, "cold_white": 0, "pure_white": 0, "warm_white": 60}
    ]
    assert set(result["auto"]["day"]) == {"pink", "blue", "cold_white", "pure_white", "warm_white"}


async def test_plant_turn_on_default_blue(hass: HomeAssistant, lamp: FakeLamp):
    lamp.channels = [0, 0, 0, 0, 0]
    lamp.power = False
    entry = plant_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await hass.services.async_call("light", "turn_on", {"entity_id": "light.plant_light"}, blocking=True)
    assert lamp.channels == [0, 20, 0, 0, 0] and lamp.power is True
    await hass.config_entries.async_unload(entry.entry_id)


async def test_product_from_advertisement_or_default(hass: HomeAssistant, lamp: FakeLamp, monkeypatch):
    # Entry without product ID: the last advertisement decides; without one it stays Reef Nano 4.0.
    found = info(name="Plant", manufacturer={65535: bytes.fromhex(PLANT_AD)})
    monkeypatch.setattr(
        "custom_components.fluvalble.coordinator.bluetooth.async_last_service_info", lambda *a, **k: found
    )
    entry = plant_entry(product_id=None)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.product.product_id == 545
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    monkeypatch.setattr(
        "custom_components.fluvalble.coordinator.bluetooth.async_last_service_info", lambda *a, **k: None
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.product.product_id == 547
    await hass.config_entries.async_unload(entry.entry_id)
