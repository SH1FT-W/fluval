"""Diagnose-Download: Zustand, Wunschzustand, Verbindung und die letzten Ereignisse."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import FluvalConfigEntry

REDACT = {"mac", "manufacturer_data", "service_uuids", "service_data"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: FluvalConfigEntry) -> dict[str, Any]:
    c = entry.runtime_data
    s = c.state
    link = c.link
    return {
        "product": {"id": c.product.product_id, "model": c.product.model, "channels": list(c.product.channels)},
        "entry": async_redact_data({"data": dict(entry.data), "options": dict(entry.options)}, REDACT),
        "state": {
            "mode": s.mode,
            "power": s.power,
            "channels": list(s.channels) if s.channels else None,
            "effect": s.effect,
            "firmware": s.firmware,
            "raw_keys": sorted(s.raw),
        },
        "programs": c.programs(),
        "desired": c.desired.as_dict(),
        "hold": c.hold,
        "available": c.available,
        "last_contact": c.last_contact.isoformat() if c.last_contact else None,
        "clock_synced": c.clock_synced.isoformat() if c.clock_synced else None,
        "connection": {
            "keep_connected": c.keep_connected,
            "connected": link.connected,
            "connected_since": link.connected_since.isoformat() if link.connected_since else None,
            "source": link.source,
            "source_id": link.source_id,
            "preferred_proxy": link.preferred,
            "preferred_retry_at": link.preferred_retry_at.isoformat() if link.preferred_retry_at else None,
            "rssi": c.rssi,
            "advertisement_source": c.advert_source,
            "mtu": link.mtu,
            "services": link.services,
            "last_error": link.last_error,
            "stats": dict(link.stats),
        },
        "stats": {
            "restores": c.stats.restores,
            "failures_in_row": c.stats.failures_in_row,
            "last_error": c.stats.last_error,
            "log": c.stats.log,
        },
    }
