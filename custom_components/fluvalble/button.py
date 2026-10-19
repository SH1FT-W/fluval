"""Knöpfe: Lampe blinken lassen, Uhr stellen, neu verbinden."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import FluvalConfigEntry
from .coordinator import FluvalCoordinator
from .entity import FluvalEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class FluvalButtonDescription(ButtonEntityDescription):
    press: Callable[[FluvalCoordinator], Awaitable[None]]
    always_available: bool = False


BUTTONS = (
    FluvalButtonDescription(
        key="identify",
        device_class=ButtonDeviceClass.IDENTIFY,
        entity_category=EntityCategory.CONFIG,
        press=lambda c: c.async_identify(),
    ),
    FluvalButtonDescription(
        key="sync_clock", entity_category=EntityCategory.CONFIG, press=lambda c: c.async_sync_clock()
    ),
    FluvalButtonDescription(
        key="reconnect",
        entity_category=EntityCategory.DIAGNOSTIC,
        press=lambda c: c.async_reconnect(),
        always_available=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: FluvalConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(FluvalButton(entry.runtime_data, d) for d in BUTTONS)


class FluvalButton(FluvalEntity, ButtonEntity):
    _platform = "button"
    entity_description: FluvalButtonDescription

    def __init__(self, coordinator: FluvalCoordinator, description: FluvalButtonDescription) -> None:
        self.entity_description = description
        super().__init__(coordinator, description.key, description.key)

    @property
    def available(self) -> bool:
        return self.entity_description.always_available or super().available

    async def async_press(self) -> None:
        await self.entity_description.press(self.coordinator)
