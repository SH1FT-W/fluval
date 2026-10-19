"""Gemeinsame Basis aller Entitäten."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import FluvalCoordinator


class FluvalEntity(CoordinatorEntity[FluvalCoordinator]):
    """unique_id = `<MAC ohne Doppelpunkte>_<key>` – wie bei der alten Integration (Entity-IDs und Verlauf bleiben)."""

    _attr_has_entity_name = True
    _platform: str

    def __init__(
        self, coordinator: FluvalCoordinator, key: str, object_id: str, translation_key: str | None = None
    ) -> None:
        super().__init__(coordinator)
        mac = coordinator.address
        self._attr_unique_id = f"{mac.replace(':', '')}_{key}"
        self._attr_translation_key = translation_key or key
        # Englische Entity-IDs wie bei der alten Integration (gilt nur für neu angelegte Entitäten).
        self.entity_id = f"{self._platform}.{coordinator.object_prefix}_{object_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac)},
            connections={(CONNECTION_BLUETOOTH, mac)},
            manufacturer=MANUFACTURER,
            model=coordinator.product.model,
            name=coordinator.config_entry.title,
            sw_version=str(coordinator.state.firmware) if coordinator.state.firmware is not None else None,
        )

    @property
    def available(self) -> bool:
        """Nur mit echten Werten der Lampe – nach Start/Neuladen ohne Verbindung „nicht verfügbar“, nie 0 %/aus."""
        return self.coordinator.available
