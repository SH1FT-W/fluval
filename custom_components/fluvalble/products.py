"""Fluval lights that speak the current FFF0/CBOR protocol, keyed by the product ID in their advertisement.

Product IDs, model names, channel order and spectrum data come from the FluvalConnect app as tabulated in
MrMooreUK/fluvalble (``core/products.py``, ``core/color.py``, Apache-2.0, see NOTICE). Only the current generation
with five channels on the FFF0 service is listed here: Reef type (385, 546, 547) and Plant type (386, 545, 548, 563).

Not included on purpose: the old encrypted generation (Plant 3.0, Marine 3.0, AquaSky 2.0, Wing Nano …), the
AquaSky 3.0 (532, FACEBD service with different CBOR keys) and the Roma & Shaker 2.0 (564, four RGBW channels).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .protocol import Levels

type XYZ = tuple[tuple[float, float, float], ...]

# CIE XYZ of each channel at 100 % (FluvalConnect spectra 540_reef.txt / 540_plant.txt).
REEF_XYZ: Final[XYZ] = (
    (21.7906687386, 9.9414676113, 38.7075292164),
    (5.4942322607, 3.2727742297, 34.4387197724),
    (8.2095208752, 1.1990824139, 43.3592065119),
    (3.0753180557, 0.2259742721, 14.7089812048),
    (25.9009870538, 26.2050707095, 43.5070407982),
)
PLANT_XYZ: Final[XYZ] = (
    (22.3920710574, 10.2409999495, 40.1904407109),
    (7.5923003076, 3.4514639199, 45.2223151866),
    (24.8292440366, 24.6469740840, 42.8391163250),
    (58.1555996981, 61.9973500549, 68.8180915292),
    (71.8207040683, 65.6183704781, 27.1511230517),
)

REEF_CHANNELS: Final = ("pink", "cyan", "blue", "purple", "cold_white")
PLANT_CHANNELS: Final = ("pink", "blue", "cold_white", "pure_white", "warm_white")
# Every channel name any supported model has (service schemas accept all of them).
ALL_CHANNELS: Final = tuple(dict.fromkeys((*REEF_CHANNELS, *PLANT_CHANNELS)))


@dataclass(frozen=True, slots=True)
class Product:
    product_id: int
    model: str
    family: str  # "reef" or "plant"
    channels: tuple[str, str, str, str, str]
    xyz: XYZ
    default_on: Levels  # turning on without any known mix: a subtle 20 % blue

    @property
    def blue(self) -> int:
        return self.channels.index("blue")


def _reef(product_id: int, model: str) -> Product:
    return Product(product_id, model, "reef", REEF_CHANNELS, REEF_XYZ, (0, 0, 20, 0, 0))


def _plant(product_id: int, model: str) -> Product:
    return Product(product_id, model, "plant", PLANT_CHANNELS, PLANT_XYZ, (0, 20, 0, 0, 0))


PRODUCTS: Final[dict[int, Product]] = {
    p.product_id: p
    for p in (
        _reef(385, "A-Sky Aqua 1025mm"),
        _reef(546, "Fluval Reef 4.0 LED"),
        _reef(547, "Fluval Reef Nano 4.0 LED"),
        _plant(386, "Fluval Plant PRO LED"),
        _plant(545, "Fluval Plant 4.0 LED"),
        _plant(548, "Fluval Plant Nano 4.0 LED"),
        _plant(563, "Fluval Siena 2.0"),
    )
}
DEFAULT_PRODUCT_ID: Final = 547  # entries of the predecessor without a known product ID were Reef Nano 4.0 lamps
DEFAULT_PRODUCT: Final = PRODUCTS[DEFAULT_PRODUCT_ID]

# Company IDs of the advertisement as Home Assistant reports them (raw bytes FF FF or FF 01, little endian).
COMPANY_IDS: Final = (0xFFFF, 0x01FF, 0xFF01)


def product_id_from_advertisement(manufacturer_data: dict[int, bytes]) -> int | None:
    """Product ID like the app reads it: company FFFF/FF01, big-endian ID at payload offset 8–9."""
    for company, data in manufacturer_data.items():
        if company in COMPANY_IDS and len(data) >= 10:
            return int.from_bytes(data[8:10], "big")
    return None


def product_for(product_id: object) -> Product | None:
    return PRODUCTS.get(product_id) if isinstance(product_id, int) and not isinstance(product_id, bool) else None
