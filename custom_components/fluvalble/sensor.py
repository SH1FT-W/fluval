"""Diagnose-Sensoren: Signal, verbunden seit, Proxy, zeitgesteuerte Effekte der Lampe."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import SIGNAL_STRENGTH_DECIBELS_MILLIWATT, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import FluvalConfigEntry
from .coordinator import FluvalCoordinator
from .entity import FluvalEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class FluvalSensorDescription(SensorEntityDescription):
    object_id: str
    value: Callable[[FluvalCoordinator], str | int | datetime | None]
    attributes: Callable[[FluvalCoordinator], dict[str, Any] | None] = lambda c: None
    always_available: bool = True


def _timed_effects(c: FluvalCoordinator) -> int | None:
    windows = c.state.effect_windows
    return None if windows is None else sum(1 for w in windows if w.enabled)


SENSORS = (
    # unique_ids rssi / last_seen / active_connection_source wie bei der alten Integration
    FluvalSensorDescription(
        key="rssi",
        object_id="signal_strength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=lambda c: c.rssi,
        attributes=lambda c: {"source": c.advert_source},
    ),
    FluvalSensorDescription(
        key="last_seen",
        translation_key="connected_since",
        object_id="last_seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda c: c.link.connected_since,
    ),
    FluvalSensorDescription(
        key="active_connection_source",
        translation_key="source",
        object_id="source",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda c: c.link.source if c.link.connected else None,
    ),
    FluvalSensorDescription(
        key="timed_effects",
        object_id="timed_effects",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=_timed_effects,
        attributes=lambda c: {"windows": [w.as_dict() for w in c.state.effect_windows or ()]},
        always_available=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: FluvalConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(FluvalSensor(entry.runtime_data, d) for d in SENSORS)


class FluvalSensor(FluvalEntity, SensorEntity):
    _platform = "sensor"
    entity_description: FluvalSensorDescription

    def __init__(self, coordinator: FluvalCoordinator, description: FluvalSensorDescription) -> None:
        self.entity_description = description
        super().__init__(coordinator, description.key, description.object_id, description.translation_key)

    @property
    def available(self) -> bool:
        return self.entity_description.always_available or super().available

    @property
    def native_value(self) -> str | int | datetime | None:
        return self.entity_description.value(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return self.entity_description.attributes(self.coordinator)
