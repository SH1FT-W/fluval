"""Bluetooth-Protokoll der Fluval Reef Nano 4.0 LED (Produkt 547).

Die Lampe hat den Bluetooth-Dienst FFF0 („SPP“). Home Assistant schreibt Befehle auf FFF2 und bekommt die
Rückmeldungen über FFF1. Beides ist unverschlüsselt:

- Befehl:       ``D1`` + CBOR-Map, z. B. ``D1 A2 01 00 02 F5`` = Modus manuell, Licht an
- Zustand:      ``D2`` (manchmal ``D1``) + CBOR-Map – kann über mehrere Bluetooth-Pakete verteilt kommen.
  Vollständig ist er erst mit mehr als 13 Einträgen; kleinere Maps sind Echos einzelner Befehle.
- Zustand holen: ``D0 FF``
- Uhr stellen:  ``CD`` + Jahr(2-stellig) Monat Tag Wochentag(1 = Montag) Stunde Minute Sekunde

Schlüssel der CBOR-Map (aus der Fluval-Connect-App, siehe README „Herkunft“):

====  ======================================================================
0     Firmware-Version (nur Rückmeldung)
1     Modus: 0 manuell, 1 Automatik, 2 Profi
2     Licht an (true) / aus (false)
3–7   Kanäle in %: Pink, Cyan, Blau, Violett, Kaltweiß
8–12  Automatik-Programm: Sonnenaufgang, Sonnenuntergang (je Std, Min, Rampe), Schlafenszeit (Std, Min
      oder FF FF), Tag- und Nachtwerte (je 5 Byte)
13    Profi-Programm: Anzahl, dann je Punkt Std, Min, 5 Kanalwerte
14    Effekt: 0 keiner, 1 Blitz, 2 Sonne und Blitz, 3 teilweise bewölkt, 4 Mondsichel
15    Zeitgesteuerte Effekte: 7 Plätze à 6 Byte (Flags, Start Std/Min, Ende Std/Min, Effekt);
      Flags Bit 0 = Sonntag … Bit 6 = Samstag, Bit 7 = aktiv
51    Vorschau des Programms (Minute 0–1439, 1440 = Vorschau beenden)
52    „find“ – Lampe blinkt zur Erkennung
====  ======================================================================
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Final

SERVICE_UUID: Final = "0000fff0-0000-1000-8000-00805f9b34fb"
WRITE_UUID: Final = "0000fff2-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID: Final = "0000fff1-0000-1000-8000-00805f9b34fb"

COMMAND_HEADER: Final = 0xD1
STATUS_HEADER: Final = 0xD2
READ_PARAMS: Final = bytes((0xD0, 0xFF))
CLOCK_OPCODE: Final = 0xCD

KEY_FIRMWARE: Final = 0
KEY_MODE: Final = 1
KEY_POWER: Final = 2
KEY_CHANNELS: Final = (3, 4, 5, 6, 7)
KEY_SUNRISE: Final = 8
KEY_SUNSET: Final = 9
KEY_SLEEP: Final = 10
KEY_DAY_LEVELS: Final = 11
KEY_NIGHT_LEVELS: Final = 12
KEY_PRO: Final = 13
KEY_EFFECT: Final = 14
KEY_EFFECT_SCHEDULE: Final = 15
KEY_FIND: Final = 52

# Reef order as in the app and the unique_ids channel_1 … channel_5; other models: products.py.
CHANNELS: Final = ("pink", "cyan", "blue", "purple", "cold_white")
MODES: Final = ("manual", "automatic", "professional")
MODE_CODE: Final = {mode: code for code, mode in enumerate(MODES)}

EFFECT_NONE: Final = "off"
# Reihenfolge wie in der App; Anzeige-Namen kommen aus den Übersetzungen.
EFFECTS: Final = {"crescent_moon": 4, "partly_cloudy": 3, "lightning": 1, "sun_and_lightning": 2}
# Namen der alten Integration – werden beim Einschalten weiter angenommen.
OLD_EFFECT_NAMES: Final = {"Crescent moon": 4, "Partly cloudy": 3, "Lightning": 1, "Sun and lightning": 2}
EFFECT_NAMES: Final = {code: name for name, code in EFFECTS.items()}

EFFECT_SLOTS: Final = 7
FULL_STATUS_KEYS: Final = 14  # so viele Einträge hat eine komplette Zustandsmeldung (App: „> 13“)
MAX_FRAME: Final = 4096
_MAX_ITEMS: Final = 64
_MAX_DEPTH: Final = 8

type Levels = tuple[int, int, int, int, int]


class ProtocolError(ValueError):
    """Ein Paket passt nicht zum erwarteten Aufbau."""


# --- CBOR (nur der kleine Teil, den die Lampe benutzt) -------------------------------------------------------


def _head(major: int, value: int) -> bytes:
    if value < 24:
        return bytes(((major << 5) | value,))
    for extra, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
        if value < 1 << (8 * size):
            return bytes(((major << 5) | extra,)) + value.to_bytes(size, "big")
    raise ProtocolError("Zahl zu groß")


def cbor_encode(value: Any) -> bytes:
    if value is None:
        return b"\xf6"
    if isinstance(value, bool):
        return b"\xf5" if value else b"\xf4"
    if isinstance(value, int):
        return _head(0, value) if value >= 0 else _head(1, -1 - value)
    if isinstance(value, bytes | bytearray):
        return _head(2, len(value)) + bytes(value)
    if isinstance(value, str):
        raw = value.encode()
        return _head(3, len(raw)) + raw
    if isinstance(value, list | tuple):
        return _head(4, len(value)) + b"".join(cbor_encode(v) for v in value)
    if isinstance(value, Mapping):
        return _head(5, len(value)) + b"".join(cbor_encode(k) + cbor_encode(v) for k, v in value.items())
    raise ProtocolError(f"Typ {type(value).__name__} wird nicht unterstützt")


def _read_length(data: bytes, pos: int) -> tuple[int, int]:
    extra = data[pos] & 0x1F
    if extra < 24:
        return extra, pos + 1
    size = {24: 1, 25: 2, 26: 4, 27: 8}.get(extra)
    if size is None:
        raise ProtocolError(f"Länge {extra} wird nicht unterstützt")
    end = pos + 1 + size
    if end > len(data):
        raise ProtocolError("abgeschnitten")
    return int.from_bytes(data[pos + 1 : end], "big"), end


def cbor_decode(data: bytes, pos: int = 0, depth: int = 0) -> tuple[Any, int]:
    """Einen Wert ab `pos` lesen → (Wert, Position danach). Unvollständige Daten → ProtocolError."""
    if depth > _MAX_DEPTH:
        raise ProtocolError("zu tief verschachtelt")
    if pos >= len(data):
        raise ProtocolError("abgeschnitten")
    item = data[pos]
    major = item >> 5
    if item in (0xF4, 0xF5):
        return item == 0xF5, pos + 1
    if item == 0xF6:
        return None, pos + 1
    if major in (0, 1):
        value, pos = _read_length(data, pos)
        return (value if major == 0 else -1 - value), pos
    if major in (2, 3):
        length, pos = _read_length(data, pos)
        if length > MAX_FRAME or pos + length > len(data):
            raise ProtocolError("abgeschnitten")
        raw = data[pos : pos + length]
        return (bytes(raw) if major == 2 else raw.decode("utf-8", "replace")), pos + length
    if major in (4, 5):
        length, pos = _read_length(data, pos)
        if length > _MAX_ITEMS:
            raise ProtocolError("zu viele Einträge")
        if major == 4:
            items = []
            for _ in range(length):
                value, pos = cbor_decode(data, pos, depth + 1)
                items.append(value)
            return items, pos
        result: dict[Any, Any] = {}
        for _ in range(length):
            key, pos = cbor_decode(data, pos, depth + 1)
            value, pos = cbor_decode(data, pos, depth + 1)
            if isinstance(key, list | dict):
                raise ProtocolError("ungültiger Schlüssel")
            result[key] = value
        return result, pos
    raise ProtocolError(f"Typ 0x{item:02x} wird nicht unterstützt")


# --- Befehle ---------------------------------------------------------------------------------------------------


def command(values: Mapping[int, Any]) -> bytes:
    """Befehl ``D1`` + CBOR-Map."""
    if not values:
        raise ProtocolError("leerer Befehl")
    return bytes((COMMAND_HEADER,)) + cbor_encode(dict(values))


def clamp(value: float) -> int:
    return max(0, min(100, round(value)))


def channel_values(levels: Levels) -> dict[int, int]:
    return {key: clamp(level) for key, level in zip(KEY_CHANNELS, levels, strict=True)}


def clock_packet(now: datetime) -> bytes:
    """Uhr der Lampe stellen (wie die App direkt nach dem Verbinden)."""
    return bytes((CLOCK_OPCODE, now.year % 100, now.month, now.day, now.isoweekday(), now.hour, now.minute, now.second))


def effect_schedule_blob(windows: list[EffectWindow]) -> bytes:
    """Zeitgesteuerte Effekte → immer alle 7 Plätze (leere Plätze = Nullen), wie die App."""
    if len(windows) > EFFECT_SLOTS:
        raise ProtocolError(f"höchstens {EFFECT_SLOTS} Zeitfenster")
    blob = bytearray(EFFECT_SLOTS * 6)
    for index, w in enumerate(windows):
        flags = sum(1 << _day_bit(day) for day in w.weekdays if 0 <= day <= 6) | (0x80 if w.enabled else 0)
        blob[index * 6 : index * 6 + 6] = bytes((flags, w.start // 60, w.start % 60, w.end // 60, w.end % 60, w.effect))
    return bytes(blob)


PRO_MAX_POINTS: Final = 12


def pro_blob(points: list[ProPoint]) -> bytes:
    """Profi-Programm wie die App (ProFragment): Anzahl, dann je Punkt Stunde, Minute, 5 Kanäle."""
    if not 1 <= len(points) <= PRO_MAX_POINTS:
        raise ProtocolError(f"1 bis {PRO_MAX_POINTS} Stützpunkte")
    if len({p.time for p in points}) != len(points):
        raise ProtocolError("jede Uhrzeit nur einmal")
    blob = bytearray([len(points)])
    for p in sorted(points, key=lambda q: q.time):
        blob += bytes((p.time // 60, p.time % 60, *(clamp(v) for v in p.levels)))
    return bytes(blob)


def _day_bit(weekday: int) -> int:
    """Wochentag (0 = Montag wie in Python) → Bit in der Lampe (Bit 0 = Sonntag)."""
    return (weekday + 1) % 7


# --- Rückmeldungen ---------------------------------------------------------------------------------------------


class FrameAssembler:
    """Setzt ``D2``-Rückmeldungen zusammen, die über mehrere Bluetooth-Pakete verteilt sind."""

    def __init__(self) -> None:
        self._buffer = b""

    def reset(self) -> None:
        self._buffer = b""

    def feed(self, chunk: bytes) -> dict[Any, Any] | None:
        """Ein Paket aufnehmen → fertige Zustands-Map oder None (noch unvollständig/ungültig)."""
        if not chunk:
            return None
        if chunk[0] in (STATUS_HEADER, COMMAND_HEADER):
            # Ein vollständiges neues Paket ersetzt ein liegengebliebenes halbes.
            whole = _try_status(chunk)
            if whole is not None or not self._buffer:
                self._buffer = b"" if whole is not None else chunk
                return whole
        if not self._buffer:
            return None  # Fortsetzung ohne Anfang – verwerfen
        self._buffer += chunk
        if len(self._buffer) > MAX_FRAME:
            self._buffer = b""
            return None
        whole = _try_status(self._buffer)
        if whole is not None:
            self._buffer = b""
        return whole


def is_full_status(update: Mapping[Any, Any]) -> bool:
    """Komplette Zustandsmeldung? Die App verlangt > 13 Einträge; sicherheitshalber reichen Modus, an/aus und alle
    fünf Kanäle – das hat kein Echo eines einzelnen Befehls (die enthalten nur die geschickten Schlüssel)."""
    return len(update) >= FULL_STATUS_KEYS or all(k in update for k in (KEY_MODE, KEY_POWER, *KEY_CHANNELS))


def _try_status(frame: bytes) -> dict[Any, Any] | None:
    try:
        value, end = cbor_decode(frame, 1)
    except ProtocolError:
        return None
    if not isinstance(value, dict) or end != len(frame):
        return None
    return value


@dataclass(frozen=True, slots=True)
class EffectWindow:
    start: int  # Minuten seit Mitternacht
    end: int
    effect: int
    weekdays: tuple[int, ...]  # 0 = Montag
    enabled: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": _hhmm(self.start),
            "end": _hhmm(self.end),
            "effect": EFFECT_NAMES.get(self.effect, str(self.effect)),
            "weekdays": list(self.weekdays),
            "enabled": self.enabled,
        }


@dataclass(frozen=True, slots=True)
class AutoProgram:
    sunrise: int
    sunrise_ramp: int
    sunset: int
    sunset_ramp: int
    sleep: int | None
    day: Levels
    night: Levels

    def as_dict(self, channels: tuple[str, ...] = CHANNELS) -> dict[str, Any]:
        return {
            "sunrise": _hhmm(self.sunrise),
            "sunrise_ramp_min": self.sunrise_ramp,
            "sunset": _hhmm(self.sunset),
            "sunset_ramp_min": self.sunset_ramp,
            "sleep": _hhmm(self.sleep) if self.sleep is not None else None,
            "day": dict(zip(channels, self.day, strict=True)),
            "night": dict(zip(channels, self.night, strict=True)),
        }


@dataclass(frozen=True, slots=True)
class ProPoint:
    time: int
    levels: Levels

    def as_dict(self, channels: tuple[str, ...] = CHANNELS) -> dict[str, Any]:
        return {"time": _hhmm(self.time), **dict(zip(channels, self.levels, strict=True))}


@dataclass(frozen=True, slots=True)
class LampState:
    """Was die Lampe zuletzt gemeldet hat. Felder, die sie (noch) nicht gemeldet hat, sind None."""

    mode: str | None = None
    power: bool | None = None
    channels: Levels | None = None
    effect: int | None = None
    firmware: int | None = None
    auto: AutoProgram | None = None
    pro: tuple[ProPoint, ...] | None = None
    effect_windows: tuple[EffectWindow, ...] | None = None
    raw: dict[int, Any] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return self.mode is not None and self.power is not None and self.channels is not None

    @property
    def lit(self) -> bool:
        """Leuchtet tatsächlich etwas? (an und mindestens ein Kanal > 0 oder ein Effekt läuft)"""
        return bool(self.power) and (any(self.channels or ()) or bool(self.effect))

    def merge(self, update: Mapping[Any, Any]) -> LampState:
        """Neue Rückmeldung einarbeiten (die Lampe schickt manchmal nur einen Teil)."""
        raw = {**self.raw, **{k: v for k, v in update.items() if isinstance(k, int)}}
        changes: dict[str, Any] = {"raw": raw}
        mode = update.get(KEY_MODE)
        if isinstance(mode, int) and not isinstance(mode, bool) and 0 <= mode < len(MODES):
            changes["mode"] = MODES[mode]
        power = update.get(KEY_POWER)
        if isinstance(power, bool | int):
            changes["power"] = bool(power)
        levels = [update.get(k) for k in KEY_CHANNELS]
        if all(isinstance(v, int) and not isinstance(v, bool) for v in levels):
            changes["channels"] = tuple(clamp(v) for v in levels)  # type: ignore[arg-type]
        elif any(v is not None for v in levels) and self.channels is not None:
            # Teilrückmeldung: nur die genannten Kanäle ersetzen.
            changes["channels"] = tuple(
                clamp(v) if isinstance(v, int) and not isinstance(v, bool) else old
                for v, old in zip(levels, self.channels, strict=True)
            )
        effect = update.get(KEY_EFFECT)
        if isinstance(effect, int) and not isinstance(effect, bool):
            changes["effect"] = effect
        firmware = update.get(KEY_FIRMWARE)
        if isinstance(firmware, int) and not isinstance(firmware, bool):
            changes["firmware"] = firmware
        if (auto := decode_auto(update)) is not None:
            changes["auto"] = auto
        if (pro := decode_pro(update.get(KEY_PRO))) is not None:
            changes["pro"] = pro
        if (windows := decode_effect_schedule(update.get(KEY_EFFECT_SCHEDULE))) is not None:
            changes["effect_windows"] = windows
        return replace(self, **changes)


def _hhmm(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _time(hour: int, minute: int) -> int | None:
    return hour * 60 + minute if hour <= 23 and minute <= 59 else None


def _levels(value: Any) -> Levels | None:
    if not isinstance(value, bytes) or len(value) < len(CHANNELS) or any(v > 100 for v in value[:5]):
        return None
    return tuple(value[: len(CHANNELS)])  # type: ignore[return-value]


def decode_auto(data: Mapping[Any, Any]) -> AutoProgram | None:
    sunrise, sunset, sleep = data.get(KEY_SUNRISE), data.get(KEY_SUNSET), data.get(KEY_SLEEP)
    day, night = _levels(data.get(KEY_DAY_LEVELS)), _levels(data.get(KEY_NIGHT_LEVELS))
    if not (
        isinstance(sunrise, bytes)
        and len(sunrise) >= 3
        and isinstance(sunset, bytes)
        and len(sunset) >= 3
        and isinstance(sleep, bytes)
        and len(sleep) >= 2
        and day is not None
        and night is not None
    ):
        return None
    rise, set_ = _time(sunrise[0], sunrise[1]), _time(sunset[0], sunset[1])
    if rise is None or set_ is None:
        return None
    return AutoProgram(rise, sunrise[2], set_, sunset[2], _time(sleep[0], sleep[1]), day, night)


def decode_pro(blob: Any) -> tuple[ProPoint, ...] | None:
    if not isinstance(blob, bytes) or not blob:
        return None
    count, size = blob[0], 2 + len(CHANNELS)
    if count > 12 or len(blob) < 1 + count * size:
        return None
    points = []
    for i in range(count):
        rec = blob[1 + i * size : 1 + (i + 1) * size]
        minute, levels = _time(rec[0], rec[1]), _levels(rec[2:])
        if minute is None or levels is None:
            return None
        points.append(ProPoint(minute, levels))
    return tuple(points)


def decode_effect_schedule(blob: Any) -> tuple[EffectWindow, ...] | None:
    if not isinstance(blob, bytes) or len(blob) % 6 or len(blob) > EFFECT_SLOTS * 6:
        return None
    windows = []
    for offset in range(0, len(blob), 6):
        flags, sh, sm, eh, em, effect = blob[offset : offset + 6]
        if not any((flags, sh, sm, eh, em, effect)):
            continue  # leerer Platz
        start, end = _time(sh, sm), _time(eh, em)
        if start is None or end is None:
            return None
        windows.append(
            EffectWindow(
                start, end, effect, tuple(d for d in range(7) if flags & (1 << _day_bit(d))), bool(flags & 0x80)
            )
        )
    return tuple(windows)
