"""Zustand der Lampe, Befehle und „gewünschten Zustand halten“.

`state` ist, was die Lampe zuletzt gemeldet hat. `desired` ist, was Home Assistant zuletzt wollte
(Modus, an/aus, Kanäle, Effekt) – gespeichert, damit es auch nach einem Neustart gilt. Meldet die Lampe
etwas anderes (spätes Echo eines alten Befehls, eigene Szene nach dem Einschalten, Stromausfall), stellt
der Koordinator den gewünschten Zustand wieder her – höchstens einmal pro Minute. Abschaltbar über den
Schalter „Zustand halten“.

Die Werte der Entitäten kommen immer von der Lampe. Solange sie seit dem Start nichts gemeldet hat oder
zu lange schweigt, sind die Entitäten „nicht verfügbar“ – nie geratene 0 %/aus.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import itertools
import logging
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.event import async_call_later, async_track_state_change_event, async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import protocol
from .ble import FluvalError, FluvalLink
from .const import (
    BACKOFF_AFTER,
    CONF_KEEP_CONNECTED,
    CONF_MAC,
    CONF_PREFERRED_PROXY,
    CONF_PRODUCT_ID,
    CONF_WEATHER,
    DEFAULT_KEEP_CONNECTED,
    DOMAIN,
    FADE_MIN_GAP,
    IDLE_DISCONNECT,
    POLL_CONNECTED,
    POLL_MAX,
    POLL_ON_DEMAND,
    PROXY_AUTO,
    RESTORE_BACKOFF,
    RSSI_STEP,
    SETTLE,
    STALE_CONNECTED,
    STALE_ON_DEMAND,
    STORAGE_VERSION,
    UNREACHABLE_ISSUE,
)
from .products import DEFAULT_PRODUCT, Product, product_for, product_id_from_advertisement
from .protocol import (
    CHANNELS,
    KEY_CHANNELS,
    KEY_EFFECT,
    KEY_EFFECT_SCHEDULE,
    KEY_MODE,
    KEY_POWER,
    KEY_PRO,
    MODE_CODE,
    EffectWindow,
    LampState,
    Levels,
    ProPoint,
    ProtocolError,
)

_LOGGER = logging.getLogger(__name__)

MANUAL = "manual"
PROFESSIONAL = "professional"

# Effekte nach Wetter
WEATHER_FX_OFF, WEATHER_FX_STORM, WEATHER_FX_ALL = "off", "storm", "storm_clouds"
STORM_STATES = ("lightning", "lightning-rainy")
CLOUD_STATES = ("partlycloudy", "cloudy")
WEATHER_FX_GAP = timedelta(hours=1)  # höchstens einmal pro Stunde
OFF_LEVELS: Levels = (0, 0, 0, 0, 0)


@dataclass(slots=True)
class Desired:
    mode: str | None = None
    power: bool | None = None
    channels: Levels | None = None
    effect: int = 0
    last_on: Levels | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "power": self.power,
            "channels": list(self.channels) if self.channels else None,
            "effect": self.effect,
            "last_on": list(self.last_on) if self.last_on else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Desired:
        def levels(v: Any) -> Levels | None:
            if isinstance(v, list) and len(v) == len(CHANNELS):
                return tuple(protocol.clamp(x) for x in v)  # type: ignore[return-value]
            return None

        mode = data.get("mode")
        power = data.get("power")
        return cls(
            mode=mode if mode in protocol.MODES else None,
            power=power if isinstance(power, bool) else None,
            channels=levels(data.get("channels")),
            effect=int(data.get("effect") or 0),
            last_on=levels(data.get("last_on")),
        )


@dataclass(slots=True)
class Stats:
    restores: int = 0
    failures_in_row: int = 0
    last_error: str | None = None
    log: list[str] = field(default_factory=list)


class FluvalCoordinator(DataUpdateCoordinator[LampState]):
    """Fragt die Lampe ab, nimmt ihre Meldungen an und führt alle Befehle aus."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.keep_connected = bool(entry.options.get(CONF_KEEP_CONNECTED, DEFAULT_KEEP_CONNECTED))
        self._base_interval = POLL_CONNECTED if self.keep_connected else POLL_ON_DEMAND
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=self._base_interval,
            always_update=True,
        )
        self.address: str = entry.data[CONF_MAC].upper()
        self.product: Product = self._detect_product(hass, entry)
        self.link = FluvalLink(hass, self.address, entry.title, self._on_status, self._on_link_change)
        preferred = entry.options.get(CONF_PREFERRED_PROXY, PROXY_AUTO)
        self.link.preferred = None if preferred == PROXY_AUTO else str(preferred).upper()
        self.state = LampState()
        self.desired = Desired()
        self.weather_fx = WEATHER_FX_OFF
        self.weather_fx_minutes = 10
        self._fx_cancel: CALLBACK_TYPE | None = None
        self._fx_effect = 0  # Effekt, den das Wetter gerade gestartet hat
        self._fx_last: datetime | None = None
        self.hold = True
        self.stats = Stats()
        self.last_contact: datetime | None = None
        self.started = dt_util.utcnow()
        self.rssi: int | None = None
        self.advert_source: str | None = None
        self.clock_synced: datetime | None = None
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._adopt = False
        self._last_command: datetime | None = None
        self._last_restore: datetime | None = None
        self._commanding = 0
        self._fade: asyncio.Task[None] | None = None
        self._idle_unsub: CALLBACK_TYPE | None = None
        self._reconnect_unsub: CALLBACK_TYPE | None = None
        self._stopping = False
        self.object_prefix = "fluval"

    def _detect_product(self, hass: HomeAssistant, entry: ConfigEntry) -> Product:
        """Model from the entry (product ID saved at setup), else from the advertisement, else Reef Nano 4.0."""
        if (product := product_for(entry.data.get(CONF_PRODUCT_ID))) is not None:
            return product
        info = bluetooth.async_last_service_info(hass, self.address, connectable=False)
        if info is not None and (product := product_for(product_id_from_advertisement(info.manufacturer_data))):
            return product
        return DEFAULT_PRODUCT

    # --- Start/Stopp ----------------------------------------------------------------------------------------

    async def async_start(self) -> None:
        stored = await self._store.async_load()
        self._adopt = stored is None  # erster Start (auch nach dem Umstieg): Zustand der Lampe übernehmen
        stored = stored or {}
        self.desired = Desired.from_dict(stored.get("desired") or {})
        self.hold = bool(stored.get("hold", True))
        self.weather_fx = str(stored.get("weather_fx", WEATHER_FX_OFF))
        self.weather_fx_minutes = int(stored.get("weather_fx_minutes", 10))
        if weather := self.config_entry.options.get(CONF_WEATHER):
            self.config_entry.async_on_unload(async_track_state_change_event(self.hass, weather, self._weather_event))
        self.config_entry.async_on_unload(
            bluetooth.async_register_callback(
                self.hass,
                self._on_advertisement,
                bluetooth.BluetoothCallbackMatcher(address=self.address, connectable=False),
                bluetooth.BluetoothScanningMode.PASSIVE,
            )
        )
        if (info := bluetooth.async_last_service_info(self.hass, self.address, connectable=False)) is not None:
            self.rssi, self.advert_source = info.rssi, info.source
        self.config_entry.async_on_unload(async_track_time_change(self.hass, self._program_tick, second=5))
        # Verbinden dauert über Proxys gern 10–40 s – im Hintergrund, damit Home Assistant nicht wartet.
        self.config_entry.async_create_background_task(self.hass, self.async_refresh(), f"{DOMAIN} erster Abruf")

    @callback
    def _program_tick(self, _now: datetime) -> None:
        """Jede Minute: im Profi-Modus die Anzeige dem Programm nachführen."""
        if self.program_levels() is not None:
            self.async_update_listeners()

    async def async_stop(self) -> None:
        self._stopping = True
        self._cancel_fade()
        if self._fx_cancel:
            self._fx_cancel()
        await self._store.async_save(self._snapshot())  # nicht auf das verzögerte Speichern warten (Neuladen)
        for unsub in (self._idle_unsub, self._reconnect_unsub):
            if unsub:
                unsub()
        await self.link.stop()

    def _save(self) -> None:
        self._store.async_delay_save(self._snapshot, 2)

    def _snapshot(self) -> dict[str, Any]:
        return {
            "desired": self.desired.as_dict(),
            "hold": self.hold,
            "weather_fx": self.weather_fx,
            "weather_fx_minutes": self.weather_fx_minutes,
        }

    def note(self, text: str, level: int = logging.INFO) -> None:
        """Eintrag ins Diagnose-Protokoll (letzte 30) und ins HA-Log mit der angegebenen Stufe."""
        stamp = dt_util.now().strftime("%d.%m. %H:%M:%S")
        self.stats.log = [*self.stats.log[-29:], f"{stamp} {text}"]
        _LOGGER.log(level, "%s: %s", self.config_entry.title, text)

    # --- Verfügbarkeit ---------------------------------------------------------------------------------------

    @property
    def fresh(self) -> bool:
        """Sind die letzten Werte der Lampe noch gültig?"""
        if self.link.connected:
            return True
        stale = STALE_CONNECTED if self.keep_connected else STALE_ON_DEMAND
        return self.last_contact is not None and dt_util.utcnow() - self.last_contact < stale

    @property
    def available(self) -> bool:
        return self.state.complete and self.fresh

    @property
    def levels(self) -> Levels:
        return self.state.channels or self.desired.channels or OFF_LEVELS

    # --- Abfrage ---------------------------------------------------------------------------------------------

    async def _async_update_data(self) -> LampState:
        try:
            if self.link.should_return:
                # Läuft über einen Ersatz-Proxy: kurz trennen, damit der bevorzugte wieder zum Zug kommt.
                self.note(f"zurück zum bevorzugten Proxy (bisher {self.link.source})")
                await self.link.disconnect()
            await self.link.status()
            if self.clock_synced is None or dt_util.utcnow() - self.clock_synced > timedelta(hours=24):
                await self.async_sync_clock()
        except FluvalError as err:
            self._failure(err)
            if self.state.complete and self.fresh:
                return self.state
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="unreachable", translation_placeholders={"error": str(err)}
            ) from err
        self._success()
        return self.state

    def _failure(self, err: FluvalError) -> None:
        self.stats.failures_in_row += 1
        self.stats.last_error = str(err)
        if self.stats.failures_in_row >= BACKOFF_AFTER:
            factor = 2 ** (self.stats.failures_in_row - BACKOFF_AFTER + 1)
            self.update_interval = min(POLL_MAX, self._base_interval * factor)
        since = self.last_contact or self.started
        if dt_util.utcnow() - since >= UNREACHABLE_ISSUE:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="unreachable",
                translation_placeholders={
                    "name": self.config_entry.title,
                    "minutes": str(round((dt_util.utcnow() - since).total_seconds() / 60)),
                    "error": str(err),
                },
            )

    def _success(self) -> None:
        if self.stats.failures_in_row >= BACKOFF_AFTER:
            self.note(f"wieder erreichbar nach {self.stats.failures_in_row} Fehlversuchen")
        self.stats.failures_in_row = 0
        self.update_interval = self._base_interval
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
        self._touch_idle()

    @property
    def _issue_id(self) -> str:
        return f"unreachable_{self.config_entry.entry_id}"

    # --- Meldungen der Lampe ---------------------------------------------------------------------------------

    @callback
    def _on_status(self, update: dict[Any, Any]) -> None:
        self.state = self.state.merge(update)
        self.last_contact = dt_util.utcnow()
        if self.state.firmware is not None and protocol.is_full_status(update):
            self._update_firmware(self.state.firmware)
        if self._adopt and self.state.complete:
            self._adopt = False
            self.desired = Desired(
                mode=self.state.mode,
                power=self.state.power,
                channels=self.state.channels,
                effect=self.state.effect or 0,
                last_on=self.state.channels if any(self.state.channels or ()) else None,
            )
            self.note(f"Zustand der Lampe übernommen: {self._describe(self.state)}")
            self._save()
        if not self.link.busy and not self._commanding:
            # Von selbst gesendete Meldung (nicht Antwort auf unsere Abfrage): sofort zeigen; das Prüfen
            # (`_schedule_maintain`) übernimmt `async_update_listeners`, das hier mit aufgerufen wird.
            self.async_set_updated_data(self.state)

    def async_update_listeners(self) -> None:
        super().async_update_listeners()
        if self.last_update_success or self.state.complete:
            self._schedule_maintain()

    @callback
    def _on_link_change(self) -> None:
        if self._stopping:
            return
        self.async_update_listeners()
        if self.keep_connected and not self.link.connected and self._reconnect_unsub is None:
            # Getrennt: in ein paar Sekunden neu verbinden (nicht sofort – der Proxy braucht einen Moment).
            self._reconnect_unsub = async_call_later(self.hass, 3, self._reconnect)

    @callback
    def _reconnect(self, _now: datetime) -> None:
        self._reconnect_unsub = None
        if not self._stopping and not self.link.connected:
            self.hass.async_create_task(self.async_request_refresh())

    @callback
    def _on_advertisement(self, info: bluetooth.BluetoothServiceInfoBleak, _change: bluetooth.BluetoothChange) -> None:
        previous = (self.rssi, self.advert_source)
        self.rssi, self.advert_source = info.rssi, info.source
        if self.keep_connected and not self.link.connected and not self.link.busy and not self._stopping:
            # Die Lampe wirbt nur, wenn niemand verbunden ist – also ist sie gerade frei.
            self.hass.async_create_task(self.async_request_refresh())
        # Werbepakete kommen im Sekundentakt: Entitäten nur bei sichtbarer Änderung schreiben (anderer Proxy oder
        # RSSI um mehr als RSSI_STEP); der aktuelle Wert geht mit der nächsten Abfrage ohnehin mit.
        if previous[0] is None or previous[1] != info.source or abs(info.rssi - previous[0]) > RSSI_STEP:
            super().async_update_listeners()

    def _update_firmware(self, firmware: int) -> None:
        reg = dr.async_get(self.hass)
        device = reg.async_get_device_by_identifier((DOMAIN, self.address), self.config_entry.entry_id)
        if device is not None and device.sw_version != str(firmware):
            reg.async_update_device(device.id, sw_version=str(firmware))

    def _touch_idle(self) -> None:
        """Ohne „Verbindung halten“: nach kurzer Ruhe trennen, damit die Fluval-App verbinden kann."""
        if self.keep_connected:
            return
        if self._idle_unsub:
            self._idle_unsub()

        @callback
        def idle(_now: datetime) -> None:
            self._idle_unsub = None
            if not self.link.busy:
                self.hass.async_create_task(self.link.disconnect())

        self._idle_unsub = async_call_later(self.hass, IDLE_DISCONNECT, idle)

    # --- Zustand halten --------------------------------------------------------------------------------------

    @callback
    def _schedule_maintain(self) -> None:
        if not self._frames_to_restore():
            return
        self.config_entry.async_create_background_task(self.hass, self._maintain(), f"{DOMAIN} Zustand halten")

    def _frames_to_restore(self) -> list[dict[int, Any]]:
        want, have = self.desired, self.state
        now = dt_util.utcnow()
        if (
            not self.hold
            or self._stopping
            or not have.complete
            or self.link.busy
            or self._commanding
            or (self._fade is not None and not self._fade.done())
            or (self._last_command is not None and now - self._last_command < SETTLE)
            or (self._last_restore is not None and now - self._last_restore < RESTORE_BACKOFF)
        ):
            return []
        frames: list[dict[int, Any]] = []
        if want.mode is not None and have.mode != want.mode:
            frames.append({KEY_MODE: MODE_CODE[want.mode]})
        if want.mode != MANUAL or want.power is None:
            return frames
        if not want.power:
            if have.power:
                frames.append({KEY_POWER: False})
            return frames
        if not have.power:
            frames.append({KEY_POWER: True})
        if want.effect and have.effect != want.effect:
            frames.append({KEY_EFFECT: want.effect})
        elif not want.effect and want.channels is not None and (have.channels != want.channels or have.effect):
            frames.append({**protocol.channel_values(want.channels), KEY_EFFECT: 0})
        return frames

    async def _maintain(self) -> None:
        frames = self._frames_to_restore()
        if not frames:
            return
        self._last_restore = dt_util.utcnow()
        self.stats.restores += 1
        self.note(f"Lampe weicht ab ({self._describe(self.state)}), stelle wieder her: {self._describe_desired()}")
        try:
            await self._send(frames)
        except FluvalError as err:
            _LOGGER.debug("Wiederherstellen gescheitert: %s", err)
        self.async_set_updated_data(self.state)

    def _describe(self, s: LampState) -> str:
        parts = [s.mode or "?", "an" if s.power else "aus"]
        if s.channels:
            parts.append("/".join(str(v) for v in s.channels))
        if s.effect:
            parts.append(f"Effekt {protocol.EFFECT_NAMES.get(s.effect, s.effect)}")
        return " ".join(parts)

    def _describe_desired(self) -> str:
        d = self.desired
        return self._describe(LampState(mode=d.mode, power=d.power, channels=d.channels, effect=d.effect))

    # --- Befehle ---------------------------------------------------------------------------------------------

    async def _send(self, frames: list[dict[int, Any]]) -> None:
        self._commanding += 1
        try:
            for frame in frames:
                await self.link.send(frame)
        finally:
            self._commanding -= 1
            self._last_command = dt_util.utcnow()
        self._success()

    async def _command(self, frames: list[dict[int, Any]], check: Callable[[LampState], bool]) -> None:
        """Befehl schicken, Übernahme prüfen, notfalls einmal wiederholen; Fehler als HomeAssistantError."""
        try:
            await self._send(frames)
            if not check(self.state):
                self.note(f"Lampe hat den Befehl nicht übernommen ({self._describe(self.state)}), zweiter Versuch")
                await self._send(frames)
        except FluvalError as err:
            self.stats.last_error = str(err)
            self.async_set_updated_data(self.state)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key=err.key, translation_placeholders={"error": str(err)}
            ) from err
        self.async_set_updated_data(self.state)
        if not check(self.state):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="not_confirmed",
                translation_placeholders={"state": self._describe(self.state)},
            )

    def _manual_prefix(self) -> list[dict[int, Any]]:
        return [] if self.state.mode == MANUAL else [{KEY_MODE: MODE_CODE[MANUAL]}]

    async def async_set_levels(self, levels: Levels, *, cancel_fade: bool = True) -> None:
        """Kanäle setzen. > 0 schaltet ein, alles 0 schaltet aus (die Lampe bleibt sonst leicht an)."""
        if cancel_fade:
            self._cancel_fade()
        levels = tuple(protocol.clamp(v) for v in levels)  # type: ignore[assignment]
        on = any(levels)
        frames = self._manual_prefix()
        if on and not self.state.power:
            frames.append({KEY_POWER: True})
        frames.append({**protocol.channel_values(levels), KEY_EFFECT: 0})
        if not on and self.state.power is not False:
            frames.append({KEY_POWER: False})
        self.desired.mode, self.desired.power, self.desired.channels, self.desired.effect = MANUAL, on, levels, 0
        if on:
            self.desired.last_on = levels
        self._save()
        await self._command(
            frames, lambda s: s.mode == MANUAL and s.channels == levels and bool(s.power) == on and not s.effect
        )

    async def async_set_channel(self, channel: str, value: float) -> None:
        # Vom angezeigten Stand ausgehen, damit die anderen Kanäle beim Wechsel aus dem Profi-Modus bleiben.
        levels = list(self.shown_levels)
        levels[self.product.channels.index(channel)] = protocol.clamp(value)
        await self.async_set_levels(tuple(levels))  # type: ignore[arg-type]

    async def async_set_power(self, on: bool) -> None:
        """Nur ein/aus. Einschalten bringt die zuletzt gewollte Mischung zurück – nie 100 % Weiß."""
        self._cancel_fade()
        if not on:
            self.desired.power = False
            self._save()
            await self._command([{KEY_POWER: False}], lambda s: s.power is False)
            return
        if self.state.mode not in (None, MANUAL) and self.desired.mode != MANUAL:
            # Automatik/Profi: nur einschalten, das Programm der Lampe bestimmt die Farbe.
            self.desired.power = True
            self._save()
            await self._command([{KEY_POWER: True}], lambda s: s.power is True)
            return
        await self.async_set_levels(self.on_levels(), cancel_fade=False)

    def program_levels(self, when: datetime | None = None) -> Levels | None:
        """Im Profi-Modus: was das Programm der Lampe gerade zeigt (zwischen Punkten linear, sonst letzter Punkt)."""
        pro = self.state.pro
        if self.state.mode != PROFESSIONAL or not pro:
            return None
        local = dt_util.as_local(when or dt_util.utcnow())
        minute = local.hour * 60 + local.minute
        points = sorted(pro, key=lambda p: p.time)
        if minute < points[0].time or minute >= points[-1].time:
            return points[-1].levels
        for a, b in itertools.pairwise(points):
            if a.time <= minute < b.time:
                f = (minute - a.time) / (b.time - a.time)
                return _mix(a.levels, b.levels, f)
        return points[-1].levels  # pragma: no cover – durch die Abfrage oben unerreichbar

    @property
    def shown_levels(self) -> Levels:
        """Kanäle für die Anzeige: im Profi-Modus aus dem Programm, sonst die Meldung der Lampe."""
        return self.program_levels() or self.levels

    @property
    def lit(self) -> bool:
        if (program := self.program_levels()) is not None:
            return bool(self.state.power) and (any(program) or bool(self.state.effect))
        return self.state.lit

    def on_levels(self) -> Levels:
        """Womit eingeschaltet wird: was gerade eingestellt ist, sonst die letzte Mischung, sonst dezentes Blau."""
        for candidate in (self.state.channels, self.desired.channels, self.desired.last_on):
            if candidate and any(candidate):
                return candidate
        return self.product.default_on

    async def async_set_effect(self, effect: int) -> None:
        self._cancel_fade()
        if not effect:
            await self.async_set_levels(self.desired.channels or self.on_levels(), cancel_fade=False)
            return
        frames = self._manual_prefix()
        if not self.state.power:
            frames.append({KEY_POWER: True})
        frames.append({KEY_EFFECT: effect})
        self.desired.mode, self.desired.power, self.desired.effect = MANUAL, True, effect
        self._save()
        await self._command(frames, lambda s: s.effect == effect and s.power is True)

    # --- Zeitgesteuerte Effekte (in der Lampe gespeichert, wie in der App) ---------------------------------

    async def async_set_timed_effects(self, windows: list[EffectWindow]) -> None:
        """Bis zu 7 Zeitfenster schreiben; leere Liste = alle löschen."""
        try:
            blob = protocol.effect_schedule_blob(windows)
        except ProtocolError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="bad_windows", translation_placeholders={"error": str(err)}
            ) from err
        wanted = protocol.decode_effect_schedule(blob)
        await self._command([{KEY_EFFECT_SCHEDULE: blob}], lambda s: s.effect_windows == wanted)
        self.note(f"Zeitgesteuerte Effekte gespeichert: {len(windows)} Zeitfenster")

    # --- Profi-Programm (Tagesablauf in der Lampe, wie in der App) ----------------------------------------

    async def async_set_pro_program(self, points: list[ProPoint], activate: bool) -> None:
        """Bis zu 12 Stützpunkte schreiben; mit `activate` danach Modus „professional“ – die Lampe läuft selbst."""
        try:
            blob = protocol.pro_blob(points)
        except ProtocolError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="bad_points", translation_placeholders={"error": str(err)}
            ) from err
        wanted = protocol.decode_pro(blob)
        await self._command([{KEY_PRO: blob}], lambda s: s.pro == wanted)
        self.note(f"Profi-Programm gespeichert: {len(points)} Stützpunkte")
        if activate:
            await self.async_set_mode(PROFESSIONAL)

    # --- Effekte nach Wetter -----------------------------------------------------------------------------

    def set_weather_fx(self, mode: str | None = None, minutes: int | None = None) -> None:
        if mode is not None:
            self.weather_fx = mode
        if minutes is not None:
            self.weather_fx_minutes = minutes
        self._save()
        self.async_update_listeners()
        self._weather_check()

    def weather_effect(self) -> int:
        """Welcher Effekt zum Wetter passt (0 = keiner)."""
        entity = self.config_entry.options.get(CONF_WEATHER)
        state = self.hass.states.get(entity) if entity else None
        if state is None or self.weather_fx == WEATHER_FX_OFF:
            return 0
        if state.state in STORM_STATES:
            return protocol.EFFECTS["lightning"]
        if self.weather_fx == WEATHER_FX_ALL and state.state in CLOUD_STATES:
            return protocol.EFFECTS["partly_cloudy"]
        return 0

    @callback
    def _weather_event(self, _event: Any) -> None:
        self._weather_check()

    @callback
    def _weather_check(self) -> None:
        """Gewitter/Wolken draußen → Effekt für ein paar Minuten, höchstens einmal pro Stunde, nur wenn Licht an."""
        effect = self.weather_effect()
        s = self.state
        if not effect or self._fx_effect or not s.lit or s.mode != MANUAL or s.effect:
            return
        now = dt_util.utcnow()
        if self._fx_last and now - self._fx_last < WEATHER_FX_GAP:
            return
        self._fx_last = now
        self._fx_effect = effect
        self.config_entry.async_create_background_task(
            self.hass, self._weather_start(effect), f"{DOMAIN} Wetter-Effekt"
        )

    async def _weather_start(self, effect: int) -> None:
        try:
            await self.async_set_effect(effect)
        except HomeAssistantError as err:
            self._fx_effect = 0
            _LOGGER.warning("%s: Wetter-Effekt nicht gestartet: %s", self.name, err)
            return
        self.note(f"Wetter-Effekt {protocol.EFFECT_NAMES.get(effect)} für {self.weather_fx_minutes} Minuten")
        self._fx_cancel = async_call_later(self.hass, self.weather_fx_minutes * 60, self._weather_end)

    @callback
    def _weather_end(self, _now: datetime) -> None:
        effect, self._fx_effect, self._fx_cancel = self._fx_effect, 0, None
        if effect and self.desired.effect == effect:  # nur beenden, wenn niemand inzwischen etwas anderes wollte
            self.config_entry.async_create_background_task(
                self.hass, self._weather_stop(), f"{DOMAIN} Wetter-Effekt aus"
            )

    async def _weather_stop(self) -> None:
        try:
            await self.async_set_effect(0)
        except HomeAssistantError as err:
            _LOGGER.warning("%s: Wetter-Effekt nicht beendet: %s", self.name, err)

    async def async_set_mode(self, mode: str) -> None:
        self._cancel_fade()
        self.desired.mode = mode
        self._save()
        await self._command([{KEY_MODE: MODE_CODE[mode]}], lambda s: s.mode == mode)

    async def async_identify(self) -> None:
        await self._packet(protocol.command({protocol.KEY_FIND: "find"}))

    async def async_sync_clock(self) -> None:
        await self._packet(protocol.clock_packet(dt_util.now()))
        self.clock_synced = dt_util.utcnow()

    async def async_reconnect(self) -> None:
        """Verbindung frisch aufbauen (ersetzt das Neuladen der Integration bei hängender Verbindung)."""
        self.note("Neu verbinden angefordert")
        self.link.reset_preferred()
        await self.link.disconnect()
        await self.async_refresh()
        if not self.link.connected:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="connect_failed",
                translation_placeholders={"error": self.stats.last_error or "?"},
            )

    async def _packet(self, packet: bytes) -> None:
        try:
            await self.link.send_packet(packet)
        except FluvalError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key=err.key, translation_placeholders={"error": str(err)}
            ) from err

    def set_hold(self, on: bool) -> None:
        self.hold = on
        self._save()
        self.async_update_listeners()

    # --- Übergänge -------------------------------------------------------------------------------------------

    def _cancel_fade(self) -> None:
        if self._fade and not self._fade.done():
            self._fade.cancel()
        self._fade = None

    async def async_fade(self, target: Levels, seconds: float | None) -> None:
        """Weich überblenden: Schritte höchstens alle 2 s, der erste sofort (Fehler gehen an den Aufrufer)."""
        self._cancel_fade()
        start = self.levels if self.state.power else OFF_LEVELS
        largest = max(abs(t - c) for t, c in zip(target, start, strict=True))
        if not seconds or seconds < FADE_MIN_GAP or largest <= 1:
            await self.async_set_levels(target, cancel_fade=False)
            return
        steps = max(1, min(int(seconds / FADE_MIN_GAP), largest))
        await self.async_set_levels(_mix(start, target, 1 / steps), cancel_fade=False)
        if steps > 1:
            self._fade = self.config_entry.async_create_background_task(
                self.hass, self._run_fade(start, target, steps, seconds / steps), f"{DOMAIN} Übergang"
            )

    async def _run_fade(self, start: Levels, target: Levels, steps: int, gap: float) -> None:
        for i in range(2, steps + 1):
            await self._sleep(gap)
            try:
                await self.async_set_levels(_mix(start, target, i / steps), cancel_fade=False)
            except HomeAssistantError as err:
                _LOGGER.debug("Übergangsschritt %s/%s gescheitert: %s", i, steps, err)
        if self.state.channels != target:
            try:
                await self.async_set_levels(target, cancel_fade=False)
            except HomeAssistantError as err:
                _LOGGER.warning("%s: Zielwert des Übergangs nicht gesetzt: %s", self.config_entry.title, err)

    async def _sleep(self, seconds: float) -> None:
        """Warten über den HA-Zeitplaner (in Tests per Zeitsprung steuerbar)."""
        done: asyncio.Future[None] = self.hass.loop.create_future()

        @callback
        def wake(_now: datetime) -> None:
            if not done.done():
                done.set_result(None)

        cancel = async_call_later(self.hass, seconds, wake)
        try:
            await done
        finally:
            cancel()

    # --- Programme -------------------------------------------------------------------------------------------

    def programs(self) -> dict[str, Any]:
        s = self.state
        return {
            "mode": s.mode,
            "auto": s.auto.as_dict(self.product.channels) if s.auto else None,
            "professional": [p.as_dict(self.product.channels) for p in s.pro] if s.pro is not None else None,
            "timed_effects": [w.as_dict() for w in s.effect_windows] if s.effect_windows is not None else None,
        }


def _mix(a: Levels, b: Levels, f: float) -> Levels:
    return tuple(round(x + (y - x) * f) for x, y in zip(a, b, strict=True))  # type: ignore[return-value]


__all__ = ["KEY_CHANNELS", "Desired", "FluvalCoordinator"]
