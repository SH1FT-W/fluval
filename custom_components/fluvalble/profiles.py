"""Saved professional programs ("profiles"), kept in the config entry options.

The Fluval app keeps its profiles on the phone; the lamp itself only stores the one professional program that is
active. Home Assistant therefore keeps its own list: a profile is a name plus the points of a professional program,
stored as `{"name": str, "points": [[minute, c1, c2, c3, c4, c5], ...]}` under the option `profiles`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .const import CONF_PROFILES, PROFILE_NAME_MAX
from .protocol import CHANNELS, PRO_MAX_POINTS, ProPoint, clamp


@dataclass(frozen=True, slots=True)
class Profile:
    name: str
    points: tuple[ProPoint, ...]

    def as_option(self) -> dict[str, Any]:
        return {"name": self.name, "points": [[p.time, *p.levels] for p in self.points]}


def normalize(points: Iterable[ProPoint]) -> tuple[ProPoint, ...]:
    """Points sorted by time, so programs compare equal however the lamp or the user ordered them."""
    return tuple(sorted(points, key=lambda p: p.time))


def _point(raw: Any) -> ProPoint | None:
    if not isinstance(raw, list | tuple) or len(raw) != 1 + len(CHANNELS):
        return None
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in raw):
        return None
    if not 0 <= raw[0] < 24 * 60:
        return None
    return ProPoint(raw[0], tuple(clamp(v) for v in raw[1:]))  # type: ignore[arg-type]


def _profile(raw: Any) -> Profile | None:
    if not isinstance(raw, Mapping):
        return None
    name, points = raw.get("name"), raw.get("points")
    if not isinstance(name, str) or not 0 < len(name.strip()) <= PROFILE_NAME_MAX or not isinstance(points, list):
        return None
    parsed = [p for p in map(_point, points) if p is not None]
    if not 1 <= len(parsed) == len(points) <= PRO_MAX_POINTS or len({p.time for p in parsed}) != len(parsed):
        return None  # the lamp would refuse it (protocol.pro_blob)
    return Profile(name.strip(), normalize(parsed))


def load(options: Mapping[str, Any]) -> list[Profile]:
    """Profiles from the options; broken or duplicate entries are skipped instead of failing the setup."""
    result: list[Profile] = []
    raw = options.get(CONF_PROFILES)
    for item in raw if isinstance(raw, list) else []:
        if (profile := _profile(item)) is not None and all(p.name != profile.name for p in result):
            result.append(profile)
    return result


def dump(profiles: Iterable[Profile]) -> list[dict[str, Any]]:
    return [p.as_option() for p in profiles]


def describe(profile: Profile) -> str:
    """Short text of the points for the options dialog, e.g. `09:00 0/0/0/0/0 · 10:00 22/77/100/66/44`."""
    return " · ".join(f"{p.time // 60:02d}:{p.time % 60:02d} {'/'.join(map(str, p.levels))}" for p in profile.points)
