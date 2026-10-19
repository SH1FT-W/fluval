"""Schalter „Zustand halten“: Home Assistant stellt wieder her, was es zuletzt eingestellt hat."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import FluvalConfigEntry
from .entity import FluvalEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: FluvalConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([FluvalHold(entry.runtime_data, "hold_state", "hold_state")])


class FluvalHold(FluvalEntity, SwitchEntity):
    _platform = "switch"
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def available(self) -> bool:
        return True  # Einstellung von Home Assistant, geht auch ohne Verbindung

    @property
    def is_on(self) -> bool:
        return self.coordinator.hold

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.set_hold(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.set_hold(False)
