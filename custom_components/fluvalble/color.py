"""RGB colour ↔ the five LED channels of a lamp.

Based on the spectra of the LED colours in the FluvalConnect app (``540_reef.txt``, ``540_plant.txt``),
converted to CIE XYZ (taken from MrMooreUK/fluvalble, Apache-2.0, see NOTICE; table in ``products.py``).
A requested colour is mixed from at most three channels so that it comes as close as possible; the other way
round the channel levels give the colour Home Assistant shows.
"""

from __future__ import annotations

from itertools import combinations

from .products import REEF_XYZ, XYZ
from .protocol import Levels, clamp

_RGB_TO_XYZ = (
    (0.4124564, 0.3575761, 0.1804375),
    (0.2126729, 0.7151522, 0.0721750),
    (0.0193339, 0.1191920, 0.9503041),
)
_XYZ_TO_RGB = (
    (3.2404542, -1.5371385, -0.4985314),
    (-0.9692660, 1.8760108, 0.0415560),
    (0.0556434, -0.2040259, 1.0572252),
)
_EPS = 1e-10


def _dot(a: tuple[float, ...] | list[float], b: tuple[float, ...] | list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def _solve(m: list[list[float]], v: list[float]) -> list[float] | None:
    """Kleines Gleichungssystem (Gauß mit Pivotsuche)."""
    n = len(v)
    a = [[*m[r], v[r]] for r in range(n)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(a[r][c]))
        if abs(a[p][c]) <= _EPS:
            return None
        a[c], a[p] = a[p], a[c]
        a[c] = [x / a[c][c] for x in a[c]]
        for r in range(n):
            if r != c:
                f = a[r][c]
                a[r] = [x - f * y for x, y in zip(a[r], a[c], strict=True)]
    return [a[r][-1] for r in range(n)]


def _fit(target: tuple[float, float, float], xyz: XYZ) -> list[float]:
    """Nicht-negative Kanalmischung, die `target` (XYZ) am nächsten kommt."""
    best: tuple[float, float, list[float]] | None = None
    for count in (1, 2, 3):
        for idx in combinations(range(len(xyz)), count):
            cols = [xyz[i] for i in idx]
            sol = _solve([[_dot(a, b) for b in cols] for a in cols], [_dot(c, target) for c in cols])
            if sol is None or any(x < -_EPS for x in sol):
                continue
            levels = [0.0] * len(xyz)
            for i, x in zip(idx, sol, strict=True):
                levels[i] = max(0.0, x)
            pred = [sum(xyz[i][ax] * levels[i] for i in range(len(levels))) for ax in range(3)]
            err = sum((p - t) ** 2 for p, t in zip(pred, target, strict=True))
            drive = sum(x * x for x in levels)
            if best is None or err < best[0] - 1e-12 or (abs(err - best[0]) <= 1e-12 and drive < best[1]):
                best = (err, drive, levels)
    return best[2] if best else [0.0] * len(xyz)


def _to_linear(c: int) -> float:
    v = max(0, min(255, int(c))) / 255
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _to_srgb(c: float) -> int:
    v = max(0.0, min(1.0, c))
    v = 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055
    return max(0, min(255, round(v * 255)))


def rgb_to_levels(rgb: tuple[int, int, int], brightness: int, xyz: XYZ = REEF_XYZ) -> Levels:
    """Wunschfarbe + Helligkeit (1–255) → Kanalwerte in %; der hellste Kanal bekommt `brightness`."""
    lin = tuple(_to_linear(c) for c in rgb)
    target = (_dot(_RGB_TO_XYZ[0], lin), _dot(_RGB_TO_XYZ[1], lin), _dot(_RGB_TO_XYZ[2], lin))
    levels = _fit(target, xyz)
    peak = max(levels)
    if peak <= _EPS:
        return (0, 0, 0, 0, 0)
    scale = max(0, min(255, brightness)) / 255 * 100
    return tuple(clamp(x / peak * scale) for x in levels)  # type: ignore[return-value]


def levels_to_rgb(levels: Levels, xyz: XYZ = REEF_XYZ) -> tuple[int, int, int]:
    """Kanalwerte → angezeigte Farbe (auf volle Helligkeit normiert)."""
    mixed = [sum(col[ax] * lv / 100 for col, lv in zip(xyz, levels, strict=True)) for ax in range(3)]
    lin = [_dot(row, mixed) for row in _XYZ_TO_RGB]
    peak = max(lin)
    if peak <= _EPS:
        return (0, 0, 0)
    r, g, b = (_to_srgb(c / peak) for c in lin)
    return (r, g, b)
