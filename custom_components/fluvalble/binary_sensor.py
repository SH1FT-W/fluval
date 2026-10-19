"""Diagnose: Lampe erreichbar (verbunden oder vor Kurzem gemeldet)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import FluvalConfigEntry
from .entity import FluvalEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: FluvalConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    # unique_id `_connection` und Entity-ID `…_reachable` wie bei der alten Integration.
    async_add_entities([FluvalReachable(entry.runtime_data, "connection", "reachable", "reachable")])


class FluvalReachable(FluvalEntity, BinarySensorEntity):
    _platform = "binary_sensor"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.fresh and self.coordinator.state.complete

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.coordinator
        return {
            "gatt_connected": c.link.connected,
            "source": c.link.source,
            "rssi": c.rssi,
            "last_contact": c.last_contact.isoformat() if c.last_contact else None,
            "last_error": c.stats.last_error if c.stats.failures_in_row else None,
        }
