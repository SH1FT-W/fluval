"""Ein Schieberegler je LED-Kanal (0–100 %). Ein Wert > 0 schaltet die Lampe ein, alle auf 0 schaltet sie aus."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import FluvalConfigEntry
from .coordinator import FluvalCoordinator
from .entity import FluvalEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: FluvalConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(
        FluvalChannel(entry.runtime_data, index) for index in range(len(entry.runtime_data.product.channels))
    )
    async_add_entities([WeatherEffectMinutes(entry.runtime_data, "weather_effect_minutes", "weather_effect_minutes")])


class FluvalChannel(FluvalEntity, NumberEntity):
    _platform = "number"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator: FluvalCoordinator, index: int) -> None:
        self._index = index
        self._channel = coordinator.product.channels[index]
        # unique_id channel_1 … channel_5 by position (Reef: Pink, Cyan, Blue, Purple, Cold white, as before).
        super().__init__(coordinator, f"channel_{index + 1}", self._channel, self._channel)

    @property
    def native_value(self) -> int | None:
        # Im Profi-Modus der Wert aus dem Programm (wie das Licht), sonst die Meldung der Lampe.
        channels = self.coordinator.program_levels() or self.coordinator.state.channels
        return channels[self._index] if channels else None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_channel(self._channel, value)


class WeatherEffectMinutes(FluvalEntity, NumberEntity):
    """Wie lange ein Wetter-Effekt läuft, bevor die Lampe zur Mischung zurückkehrt."""

    _platform = "number"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 1
    _attr_native_max_value = 120
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_mode = NumberMode.BOX

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> int:
        return self.coordinator.weather_fx_minutes

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.set_weather_fx(minutes=int(value))
