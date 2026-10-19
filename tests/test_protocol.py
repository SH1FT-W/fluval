"""Paketaufbau – Beispiele aus der Fluval-Connect-App (Byte für Byte)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from custom_components.fluvalble import color, protocol
from custom_components.fluvalble.protocol import EffectWindow, FrameAssembler, LampState, ProtocolError


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({2: True}, "d1a102f5"),
        ({2: False}, "d1a102f4"),
        ({1: 1}, "d1a10101"),
        ({3: 10, 4: 20, 5: 30, 6: 40, 7: 50, 14: 0}, "d1a6030a041405181e061828071832" + "0e00"),
        ({5: 80, 14: 0}, "d1a20518500e00"),
        ({52: "find"}, "d1a1183464" + "66696e64"),
        ({51: 600}, "d1a118331902" + "58"),
        ({51: 1440}, "d1a1183319" + "05a0"),
    ],
)
def test_commands_like_app(values, expected):
    assert protocol.command(values).hex() == expected


def test_clock_like_app():
    assert protocol.clock_packet(datetime(2026, 10, 7, 19, 42, 0, tzinfo=UTC)).hex() == "cd1a0a0703132a00"


def test_cbor_roundtrip_and_errors():
    value = {0: 14, 1: -5, 2: b"\x01" * 30, 3: "x" * 30, 4: [1, 2, 70000, 2**40], 5: None, 6: False}
    raw = protocol.cbor_encode(value)
    assert protocol.cbor_decode(raw) == (value, len(raw))
    with pytest.raises(ProtocolError):
        protocol.cbor_encode(1.5)
    with pytest.raises(ProtocolError):
        protocol.cbor_encode(2**70)
    with pytest.raises(ProtocolError):
        protocol.command({})
    for bad in (b"", b"\x19\x01", b"\x1c", b"\x42\x01", b"\xa1\x80\x01", b"\xfa", b"\x98\x41" + b"\x00" * 65):
        with pytest.raises(ProtocolError):
            protocol.cbor_decode(bad)
    deep = b"\x81" * 10 + b"\x00"
    with pytest.raises(ProtocolError):
        protocol.cbor_decode(deep)


def test_assembler_pieces_and_garbage():
    asm = FrameAssembler()
    frame = b"\xd2" + protocol.cbor_encode({i: i for i in range(20)})
    assert asm.feed(frame[:20]) is None
    assert asm.feed(frame[20:]) == {i: i for i in range(20)}
    assert asm.feed(b"") is None
    assert asm.feed(b"\x00\x01") is None  # Fortsetzung ohne Anfang
    # halbes Paket, dann ein vollständiges neues → das neue gilt
    asm.feed(frame[:10])
    assert asm.feed(b"\xd2\xa1\x02\xf5") == {2: True}
    # zu groß → verworfen
    asm.feed(b"\xd2\x5a\x00\x01\x00\x00")
    assert asm.feed(b"\x00" * 5000) is None
    # kein Map
    assert asm.feed(b"\xd2\x01") is None


def test_state_merge_partial_and_programs():
    full = {
        0: 14,
        1: 0,
        2: True,
        3: 1,
        4: 2,
        5: 3,
        6: 4,
        7: 5,
        14: 0,
        8: bytes((7, 0, 60)),
        9: bytes((17, 0, 60)),
        10: bytes((0xFF, 0xFF)),
        11: bytes(5),
        12: bytes(5),
        13: bytes((1, 9, 30, 1, 2, 3, 4, 5)),
        15: bytes(42),
    }
    s = LampState().merge(full)
    assert s.complete and s.lit and s.firmware == 14
    assert s.auto is not None and s.auto.sleep is None and s.auto.as_dict()["sunrise"] == "07:00"
    assert s.pro is not None and s.pro[0].as_dict()["time"] == "09:30"
    assert s.effect_windows == ()
    s = s.merge({6: 50})
    assert s.channels == (1, 2, 3, 50, 5)
    s = s.merge({1: 9, 2: "x", 3: True})  # unsinnige Werte ändern nichts
    assert s.mode == "manual" and s.channels == (1, 2, 3, 50, 5)
    assert LampState().merge({3: 5}).channels is None


@pytest.mark.parametrize(
    "data",
    [
        {8: bytes((25, 0, 0)), 9: bytes(3), 10: bytes(2), 11: bytes(5), 12: bytes(5)},
        {8: bytes(3), 9: bytes(3), 10: bytes(2), 11: bytes((101, 0, 0, 0, 0)), 12: bytes(5)},
        {8: bytes(3)},
    ],
)
def test_bad_auto(data):
    assert protocol.decode_auto(data) is None


def test_full_status_detection():
    assert protocol.is_full_status(dict.fromkeys(range(16), 0))
    assert protocol.is_full_status({1: 0, 2: True, 3: 1, 4: 1, 5: 1, 6: 1, 7: 1})  # knappe Meldung ohne Programme
    assert not protocol.is_full_status({3: 1, 4: 1, 5: 1, 6: 1, 7: 1, 14: 0})  # Echo des Kanal-Befehls
    assert not protocol.is_full_status({2: True})


def test_bad_pro_and_effects():
    assert protocol.decode_pro(b"") is None
    assert protocol.decode_pro(bytes((13,))) is None
    assert protocol.decode_pro(bytes((1, 25, 0, 0, 0, 0, 0, 0))) is None
    assert protocol.decode_effect_schedule(bytes(5)) is None
    assert protocol.decode_effect_schedule(bytes((0x80, 25, 0, 1, 0, 1))) is None


def test_effect_schedule_weekday_bits():
    """App: Bit 0 = Sonntag. Hier: 0 = Montag (wie Python)."""
    blob = protocol.effect_schedule_blob([EffectWindow(12 * 60, 12 * 60 + 30, 1, (0, 6))])
    assert blob[:6] == bytes((0x80 | 0b0000011, 12, 0, 12, 30, 1)) and len(blob) == 42
    assert protocol.decode_effect_schedule(blob) == (EffectWindow(720, 750, 1, (0, 6)),)
    assert EffectWindow(0, 1, 9, ()).as_dict()["effect"] == "9"
    with pytest.raises(ProtocolError):
        protocol.effect_schedule_blob([EffectWindow(0, 1, 1, ())] * 8)


def test_color_mapping():
    assert color.rgb_to_levels((0, 0, 0), 255) == (0, 0, 0, 0, 0)
    assert color.levels_to_rgb((0, 0, 0, 0, 0)) == (0, 0, 0)
    white = color.rgb_to_levels((255, 255, 255), 255)
    assert max(white) == 100 and white[4] == 100
    assert color.levels_to_rgb((22, 77, 100, 66, 44)) == (114, 74, 255)
    red = color.rgb_to_levels((255, 0, 0), 128)
    assert max(red) == 50
