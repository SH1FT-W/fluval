"""Die Lampe als RGB-Licht.

- nur `rgb_color`            → Farbe aus den fünf Kanälen gemischt, Helligkeit bleibt
- `rgb_color` + `brightness` → Farbe, hellster Kanal = `brightness`
- nur `brightness`           → aktuelle (oder zuletzt eingeschaltete) Mischung heller/dunkler
- nichts                     → war sie aus: letzte Mischung (alt: 100 % Weiß, wenn alle Kanäle 0 waren)
- `effect`                   → Effekt der Lampe („off“ = zurück zur Mischung)
- `transition`               → Übergang in kleinen Schritten
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ATTR_TRANSITION,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import FluvalConfigEntry
from .color import levels_to_rgb, rgb_to_levels
from .const import DOMAIN, WEEKDAYS
from .entity import FluvalEntity
from .protocol import (
    EFFECT_NAMES,
    EFFECT_NONE,
    EFFECTS,
    OLD_EFFECT_NAMES,
    EffectWindow,
    Levels,
    ProPoint,
    clamp,
)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: FluvalConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([FluvalLight(entry.runtime_data, "light", "light")])


def scaled(levels: Levels, brightness: int) -> Levels:
    """Mischung beibehalten, hellster Kanal = brightness (1–255)."""
    top = max(levels)
    target = brightness / 2.55
    if top == 0:
        return levels
    return tuple(clamp(v * target / top) for v in levels)  # type: ignore[return-value]


class FluvalLight(FluvalEntity, LightEntity):
    _platform = "light"
    _attr_supported_color_modes = {ColorMode.RGB}  # noqa: RUF012 (HA-Konvention)
    _attr_supported_features = LightEntityFeature.EFFECT | LightEntityFeature.TRANSITION
    _attr_effect_list = [EFFECT_NONE, *EFFECTS]  # noqa: RUF012

    @property
    def is_on(self) -> bool:
        return self.coordinator.lit

    @property
    def color_mode(self) -> ColorMode:
        return ColorMode.ONOFF if self.coordinator.state.effect else ColorMode.RGB

    @property
    def effect(self) -> str:
        return EFFECT_NAMES.get(self.coordinator.state.effect or 0, EFFECT_NONE)

    @property
    def brightness(self) -> int | None:
        if self.coordinator.state.effect:
            return None
        return max(1, round(max(self.coordinator.shown_levels) * 2.55))

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        if self.coordinator.state.effect:
            return None
        levels = self.coordinator.shown_levels
        xyz = self.coordinator.product.xyz
        return levels_to_rgb(levels if any(levels) else self.coordinator.on_levels(), xyz)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Die echten Kanalwerte in % (für Dashboards)."""
        channels = self.coordinator.product.channels
        return {"channels": dict(zip(channels, self.coordinator.shown_levels, strict=True))}

    async def async_turn_on(self, **kwargs: Any) -> None:
        c = self.coordinator
        if (effect := kwargs.get(ATTR_EFFECT)) is not None:
            await c.async_set_effect(EFFECTS.get(effect) or OLD_EFFECT_NAMES.get(effect, 0))
            return
        rgb, brightness = kwargs.get(ATTR_RGB_COLOR), kwargs.get(ATTR_BRIGHTNESS)
        if rgb is None and brightness is None:
            if self.is_on:
                return
            if kwargs.get(ATTR_TRANSITION):
                await c.async_fade(c.on_levels(), kwargs[ATTR_TRANSITION])
            else:
                await c.async_set_power(True)
            return
        base = c.on_levels()
        if rgb is not None:
            current = max(base) * 2.55 if self.is_on else 255
            levels = rgb_to_levels(tuple(rgb), int(brightness if brightness is not None else current), c.product.xyz)
        else:
            levels = scaled(base, int(brightness or 255))
        await c.async_fade(levels, kwargs.get(ATTR_TRANSITION))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Wirklich aus (Licht-aus-Befehl). Die Kanalwerte bleiben für das nächste Einschalten."""
        c = self.coordinator
        if kwargs.get(ATTR_TRANSITION) and self.is_on:
            await c.async_fade((0, 0, 0, 0, 0), kwargs[ATTR_TRANSITION])
            return
        await c.async_set_power(False)

    async def async_set_channels(self, transition: float | None = None, **channels: int) -> None:
        """Dienst fluvalble.set_channels: Kanäle in %, nicht genannte bleiben wie sie sind."""
        c = self.coordinator
        names = c.product.channels
        if unknown := sorted(set(channels) - set(names)):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_channel",
                translation_placeholders={"channels": ", ".join(unknown), "valid": ", ".join(names)},
            )
        levels = tuple(channels.get(name, value) for name, value in zip(names, c.levels, strict=True))
        await c.async_fade(levels, transition)  # type: ignore[arg-type]

    async def async_set_timed_effects(self, windows: list[dict[str, Any]]) -> None:
        """Dienst fluvalble.set_timed_effects: bis zu 7 Zeitfenster in die Lampe schreiben (leer = alle löschen)."""
        await self.coordinator.async_set_timed_effects(
            [
                EffectWindow(
                    w["start"],
                    w["end"],
                    EFFECTS[w["effect"]],
                    tuple(sorted(WEEKDAYS[d] for d in w.get("weekdays") or WEEKDAYS)),
                    w.get("enabled", True),
                )
                for w in windows
            ]
        )

    async def async_set_pro_program(self, points: list[dict[str, Any]], activate: bool = True) -> None:
        """Dienst fluvalble.set_pro_program: Tagesablauf als Profi-Programm in die Lampe."""
        await self.coordinator.async_set_pro_program(
            [ProPoint(p["time"], tuple(p.get(c, 0) for c in self.coordinator.product.channels)) for p in points],
            activate,
        )

    async def async_get_programs(self) -> dict[str, Any]:
        """Dienst fluvalble.get_programs: Automatik-, Profi-Programm und zeitgesteuerte Effekte der Lampe."""
        return self.coordinator.programs()
