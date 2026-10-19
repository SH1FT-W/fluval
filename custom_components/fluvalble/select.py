"""Modus der Lampe: manuell (Home Assistant steuert), Automatik oder Profi (Programm der Lampe)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import FluvalConfigEntry
from .entity import FluvalEntity
from .protocol import MODES

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: FluvalConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(
        [
            FluvalMode(entry.runtime_data, "mode", "mode"),
            WeatherEffects(entry.runtime_data, "weather_effects", "weather_effects"),
        ]
    )


class FluvalMode(FluvalEntity, SelectEntity):
    _platform = "select"
    _attr_options = list(MODES)  # noqa: RUF012 (HA-Konvention)

    @property
    def current_option(self) -> str | None:
        return self.coordinator.state.mode

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_mode(option)


class WeatherEffects(FluvalEntity, SelectEntity):
    """Effekte nach echtem Wetter: aus / nur Gewitter / Gewitter + Wolken."""

    _platform = "select"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = ["off", "storm", "storm_clouds"]  # noqa: RUF012

    @property
    def available(self) -> bool:
        return True

    @property
    def current_option(self) -> str:
        return self.coordinator.weather_fx

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {"weather_entity": self.coordinator.config_entry.options.get("weather_entity")}

    async def async_select_option(self, option: str) -> None:
        self.coordinator.set_weather_fx(mode=option)
