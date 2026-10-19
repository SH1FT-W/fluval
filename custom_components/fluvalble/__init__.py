"""Fluval LED lights of the current Bluetooth generation (successor of MrMooreUK/fluvalble, same domain and IDs)."""

from __future__ import annotations

import logging

from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.helpers import config_validation as cv, entity_registry as er, service
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import slugify
import voluptuous as vol

from .const import CONF_KEEP_CONNECTED, DOMAIN, WEEKDAYS
from .coordinator import FluvalCoordinator
from .products import ALL_CHANNELS
from .protocol import EFFECTS

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

type FluvalConfigEntry = ConfigEntry[FluvalCoordinator]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
_PERCENT = vol.All(vol.Coerce(int), vol.Range(min=0, max=100))
_PRO_CHANNELS: dict[vol.Marker, object] = {vol.Optional(c, default=0): _PERCENT for c in ALL_CHANNELS}


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Dienste `set_channels` (alle Kanäle in einem Befehl) und `get_programs` (Programme der Lampe lesen)."""
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        "set_channels",
        entity_domain=LIGHT_DOMAIN,
        schema={
            **{vol.Optional(channel): _PERCENT for channel in ALL_CHANNELS},
            vol.Optional("transition"): vol.All(vol.Coerce(float), vol.Range(min=0, max=21600)),
        },
        func="async_set_channels",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        "get_programs",
        entity_domain=LIGHT_DOMAIN,
        schema={},
        func="async_get_programs",
        supports_response=SupportsResponse.ONLY,
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        "set_timed_effects",
        entity_domain=LIGHT_DOMAIN,
        schema={
            vol.Required("windows"): vol.All(
                cv.ensure_list,
                [
                    vol.Schema(
                        {
                            vol.Required("start"): _minutes,
                            vol.Required("end"): _minutes,
                            vol.Required("effect"): vol.In(list(EFFECTS)),
                            vol.Optional("weekdays"): vol.All(cv.ensure_list, [vol.In(list(WEEKDAYS))]),
                            vol.Optional("enabled", default=True): cv.boolean,
                        }
                    )
                ],
                vol.Length(max=7),
            )
        },
        func="async_set_timed_effects",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        "set_pro_program",
        entity_domain=LIGHT_DOMAIN,
        schema={
            vol.Required("points"): vol.All(
                cv.ensure_list,
                [vol.Schema({vol.Required("time"): _minutes, **_PRO_CHANNELS})],
                vol.Length(min=1, max=12),
            ),
            vol.Optional("activate", default=True): cv.boolean,
        },
        func="async_set_pro_program",
    )
    return True


def _minutes(value: object) -> int:
    t = cv.time(value)
    return t.hour * 60 + t.minute


async def async_setup_entry(hass: HomeAssistant, entry: FluvalConfigEntry) -> bool:
    coordinator = FluvalCoordinator(hass, entry)
    coordinator.object_prefix = _object_prefix(hass, entry, coordinator.address)
    entry.runtime_data = coordinator
    await coordinator.async_start()
    entry.async_on_unload(coordinator.async_stop)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FluvalConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _object_prefix(hass: HomeAssistant, entry: ConfigEntry, address: str) -> str:
    """Vorne an neuen Entity-IDs: wie beim Licht (z. B. `aquarium_fluval_reef_nano_4_0_led`), sonst aus dem Titel."""
    light = er.async_get(hass).async_get_entity_id("light", DOMAIN, f"{address.replace(':', '')}_light")
    if light:
        return light.split(".", 1)[1].removesuffix("_light")
    return slugify(entry.title) or "fluval"


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Eintrag der alten Integration übernehmen (Version 2.1 → 2.2).

    Die alte Integration hatte vier Optionen; davon bleibt nur „Verbindung halten“ (alt: Aktivzeit 0 = dauerhaft).
    Die Daten (MAC, Modell, Produkt-ID, Werbedaten) bleiben unverändert.
    """
    if entry.version > 2:
        return False
    if entry.version == 1:
        hass.config_entries.async_update_entry(entry, version=2, minor_version=1)
    if entry.minor_version < 2:
        active_time = entry.options.get("active_time", 0)
        hass.config_entries.async_update_entry(entry, options={CONF_KEEP_CONNECTED: active_time == 0}, minor_version=2)
        _LOGGER.info("Eintrag %s übernommen", entry.title)
    return True
