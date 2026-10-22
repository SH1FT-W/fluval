"""Die Integration als Ganzes gegen die nachgebaute Lampe."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er, issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.fluvalble.ble import NotInRange
from custom_components.fluvalble.const import DOMAIN, POLL_MAX
from custom_components.fluvalble.diagnostics import async_get_config_entry_diagnostics

from .conftest import ENTRY_ID, MAC, UID, FakeLamp, old_entry

P = "aquarium_fluval_reef_nano_4_0_led"
LIGHT = f"light.{P}_light"
MODE = f"select.{P}_mode"
REACH = f"binary_sensor.{P}_reachable"
NUM = {c: f"number.{P}_{c}" for c in ("pink", "cyan", "blue", "purple", "cold_white")}

# So stehen die 14 Entitäten in einer bestehenden Installation (Entity-ID, unique_id).
OLD = [
    (REACH, f"{UID}_connection"),
    (f"button.{P}_identify", f"{UID}_identify"),
    (f"button.{P}_sync_clock", f"{UID}_sync_clock"),
    (MODE, f"{UID}_mode"),
    (f"sensor.{P}_signal_strength", f"{UID}_rssi"),
    (f"sensor.{P}_last_seen", f"{UID}_last_seen"),
    (f"sensor.{P}_source", f"{UID}_active_connection_source"),
    (f"sensor.{P}_connection_mode", f"{UID}_connection_mode"),
    (LIGHT, f"{UID}_light"),
    (NUM["pink"], f"{UID}_channel_1"),
    (NUM["cyan"], f"{UID}_channel_2"),
    (NUM["blue"], f"{UID}_channel_3"),
    (NUM["purple"], f"{UID}_channel_4"),
    (NUM["cold_white"], f"{UID}_channel_5"),
]


@pytest.fixture
def registered(hass: HomeAssistant):
    """Registry wie in einer bestehenden Installation (Entity-IDs mit Raum-Präfix „aquarium_“)."""
    entry = old_entry()
    entry.add_to_hass(hass)
    reg = er.async_get(hass)
    for entity_id, uid in OLD:
        domain, object_id = entity_id.split(".")
        reg.async_get_or_create(domain, DOMAIN, uid, suggested_object_id=object_id, config_entry=entry)
    return entry


@pytest.fixture
async def prod(hass: HomeAssistant, registered, lamp: FakeLamp):
    assert await hass.config_entries.async_setup(registered.entry_id)
    await hass.async_block_till_done()
    yield registered
    if registered.state.recoverable:
        await hass.config_entries.async_unload(registered.entry_id)
        await hass.async_block_till_done()


async def tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float = 30) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def call(hass: HomeAssistant, domain: str, service: str, entity_id: str, **data) -> None:
    await hass.services.async_call(domain, service, {"entity_id": entity_id, **data}, blocking=True)
    await hass.async_block_till_done()


async def set_value(hass: HomeAssistant, channel: str, value: int) -> None:
    await call(hass, "number", "set_value", NUM[channel], value=value)


# --- Umstieg ----------------------------------------------------------------------------------------------------


async def test_old_entry_keeps_ids_and_options(hass: HomeAssistant, prod, lamp: FakeLamp):
    """Gleiche unique_ids wie die alte Integration → Automationen, Skripte, Dashboards laufen weiter."""
    reg = er.async_get(hass)
    for entity_id, uid in OLD:
        assert reg.async_get_entity_id(entity_id.split(".")[0], DOMAIN, uid) == entity_id
    assert prod.minor_version == 2
    assert prod.options == {"keep_connected": True}
    assert hass.states.get(LIGHT).state == STATE_ON
    assert hass.states.get(MODE).state == "manual"
    assert hass.states.get(NUM["pink"]).state == "22"
    assert hass.states.get(LIGHT).attributes["rgb_color"] == (114, 74, 255)  # wie die alte Integration
    assert hass.states.get(REACH).attributes["gatt_connected"] is True
    # neue Entitäten bekommen dasselbe Präfix
    assert hass.states.get(f"switch.{P}_hold_state").state == STATE_ON
    assert hass.states.get(f"button.{P}_reconnect")
    assert hass.states.get(f"sensor.{P}_timed_effects").state == "1"
    # weggefallen: Verbindungsmodus (war nur ein Spiegel der Option)
    assert hass.states.get(f"sensor.{P}_connection_mode") is None
    # Firmware ins Gerät
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, MAC), ENTRY_ID)
    assert device is not None and device.sw_version == "14"
    # Uhr nach dem Verbinden gestellt
    assert len(lamp.clock) >= 1


async def test_on_demand_option_migrated(hass: HomeAssistant, lamp: FakeLamp):
    entry = old_entry(active_time=120, lamp_profile="auto", ping_interval=10, restore_previous_mode=False)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.options == {"keep_connected": False}
    await hass.config_entries.async_unload(entry.entry_id)


async def test_version1_and_future_versions(hass: HomeAssistant, lamp: FakeLamp):
    entry = old_entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, version=1, minor_version=1)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert (entry.version, entry.minor_version) == (2, 2)
    await hass.config_entries.async_unload(entry.entry_id)
    hass.config_entries.async_update_entry(entry, version=3)
    assert not await hass.config_entries.async_setup(entry.entry_id)


# --- Verfügbarkeit ----------------------------------------------------------------------------------------------


async def test_unavailable_until_lamp_answers(
    hass: HomeAssistant, registered, lamp: FakeLamp, freezer: FrozenDateTimeFactory
):
    """Alter Fehler: nach dem Neuladen zeigte HA 0 %/aus, obwohl die Lampe nicht verbunden war."""
    lamp.in_range = False
    assert await hass.config_entries.async_setup(registered.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(LIGHT).state == STATE_UNAVAILABLE
    assert hass.states.get(NUM["blue"]).state == STATE_UNAVAILABLE
    assert hass.states.get(REACH).state == STATE_OFF
    with pytest.raises(HomeAssistantError, match="not reachable by any"):
        await hass.data["entity_components"]["number"].get_entity(NUM["blue"]).async_set_native_value(10)
    lamp.in_range = True
    await tick(hass, freezer)
    assert hass.states.get(LIGHT).state == STATE_ON
    assert hass.states.get(NUM["blue"]).state == "100"


async def test_stale_then_unavailable_and_repair_issue(
    hass: HomeAssistant, prod, lamp: FakeLamp, freezer: FrozenDateTimeFactory
):
    issues = ir.async_get(hass)
    await lamp.client.disconnect()
    lamp.offline = True
    await tick(hass, freezer, 5)
    assert hass.states.get(LIGHT).state == STATE_ON  # kurze Funkstille: letzte Werte gelten noch
    await tick(hass, freezer, 200)
    assert hass.states.get(LIGHT).state == STATE_UNAVAILABLE
    assert hass.states.get(REACH).state == STATE_OFF
    assert "BleakOutOfConnectionSlots" in hass.states.get(REACH).attributes["last_error"]
    assert issues.async_get_issue(DOMAIN, f"unreachable_{ENTRY_ID}") is None
    for _ in range(12):
        await tick(hass, freezer, 300)  # seltener versuchen (bis alle 5 Minuten)
    assert issues.async_get_issue(DOMAIN, f"unreachable_{ENTRY_ID}") is not None
    lamp.offline = False
    await tick(hass, freezer, 300)
    assert hass.states.get(LIGHT).state == STATE_ON
    assert issues.async_get_issue(DOMAIN, f"unreachable_{ENTRY_ID}") is None
    assert prod.runtime_data.update_interval == timedelta(seconds=30)


async def test_long_outage_backoff_does_not_overflow(hass: HomeAssistant, prod):
    """A lamp unreachable for hours: the backoff stays at POLL_MAX instead of overflowing timedelta."""
    c = prod.runtime_data
    c.stats.failures_in_row = 60
    c._failure(NotInRange("out of range"))
    assert c.update_interval == POLL_MAX


async def test_reconnects_after_drop(hass: HomeAssistant, prod, lamp: FakeLamp, freezer: FrozenDateTimeFactory):
    c = prod.runtime_data
    await lamp.client.disconnect()
    assert not c.link.connected and c.link.stats["disconnects"] == 1
    await tick(hass, freezer, 4)
    assert lamp.connects == 2
    assert hass.states.get(REACH).attributes["gatt_connected"] is True


async def test_advertisement_triggers_connect(hass: HomeAssistant, prod, lamp: FakeLamp):
    c = prod.runtime_data
    await c.link.disconnect()
    lamp.offline = True
    await hass.async_block_till_done()
    lamp.offline = False
    info = type("Info", (), {"rssi": -64, "source": "02:00:00:00:00:A1"})()
    c._on_advertisement(info, None)
    await hass.async_block_till_done()
    assert c.rssi == -64
    assert c.link.connected


async def test_advertisement_rssi_throttled(hass: HomeAssistant, prod):
    """Werbepakete schreiben die Entitäten nur bei sichtbarer RSSI-Änderung oder anderem Proxy."""
    c = prod.runtime_data
    calls: list[None] = []
    remove = c.async_add_listener(lambda: calls.append(None))
    c.rssi, c.advert_source = None, None

    def advert(rssi: int, source: str = "AA:BB:CC:00:00:01") -> None:
        c._on_advertisement(type("Info", (), {"rssi": rssi, "source": source})(), None)

    advert(-60)
    advert(-62)  # kleine Schwankung: nichts schreiben
    advert(-70)  # deutlich schwächer
    advert(-70, "AA:BB:CC:00:00:02")  # anderer Proxy
    assert len(calls) == 3 and c.rssi == -70
    remove()


async def test_notify_error_does_not_break_link(hass: HomeAssistant, prod):
    """Ein Fehler beim Verarbeiten einer Rückmeldung wird geloggt, der bleak-Rückruf läuft weiter."""
    link = prod.runtime_data.link
    original = link._on_status
    link._on_status = lambda frame: (_ for _ in ()).throw(ValueError("kaputt"))
    link._on_notify(None, bytearray(b"\xd2\xa1\x01\x01"))  # darf nicht abstürzen
    link._on_status = original


async def test_write_error_reconnects(hass: HomeAssistant, prod, lamp: FakeLamp):
    lamp.drop_on_write = True
    with pytest.raises(HomeAssistantError, match="Could not connect"):
        await set_value(hass, "blue", 50)
    await set_value(hass, "blue", 50)  # nächster Befehl verbindet neu
    assert lamp.channels[2] == 50


async def test_hanging_proxy_times_out(hass: HomeAssistant, prod, lamp: FakeLamp):
    """Ein Befehl darf nie minutenlang hängen (passiert mit der alten Integration)."""
    lamp.hang = True
    with (
        patch("custom_components.fluvalble.ble.OPERATION_TIMEOUT", 0.05),
        pytest.raises(HomeAssistantError, match="did not answer"),
    ):
        await set_value(hass, "blue", 50)
    lamp.hang = False
    assert not prod.runtime_data.link.connected
    await set_value(hass, "blue", 50)
    assert lamp.channels[2] == 50


async def test_slow_connect_is_not_cancelled(hass: HomeAssistant, prod, lamp: FakeLamp):
    """Ein langsamer Verbindungsaufbau läuft zu Ende – abbrechen hinterließe eine verwaiste Proxy-Verbindung."""
    link = prod.runtime_data.link
    await link.disconnect()
    lamp.connect_delay = 0.2
    with patch("custom_components.fluvalble.ble.OPERATION_TIMEOUT", 0.1):
        await set_value(hass, "blue", 50)
    assert lamp.channels[2] == 50 and link.connected


async def test_queue_wait_is_limited(hass: HomeAssistant, prod, lamp: FakeLamp):
    """Wer hinter einem hängenden Vorgang wartet, bekommt nach QUEUE_TIMEOUT eine Absage."""
    link = prod.runtime_data.link
    await link._lock.acquire()
    try:
        with (
            patch("custom_components.fluvalble.ble.QUEUE_TIMEOUT", 0.05),
            pytest.raises(HomeAssistantError, match="did not answer"),
        ):
            await set_value(hass, "blue", 50)
    finally:
        link._lock.release()


async def test_lamp_without_fluval_service(hass: HomeAssistant, registered, lamp: FakeLamp):
    lamp.without_fff0 = True
    assert await hass.config_entries.async_setup(registered.entry_id)
    await hass.async_block_till_done()
    c = registered.runtime_data
    assert "FFF0" in (c.stats.last_error or "")
    assert not c.link.connected


async def test_no_answer_to_status(hass: HomeAssistant, prod, lamp: FakeLamp):
    lamp.silent = True
    with pytest.raises(HomeAssistantError, match="did not answer"):
        await set_value(hass, "blue", 50)
    lamp.silent = False


# --- Kanäle wie im Tageslicht-Skript ----------------------------------------------------------------------------


async def test_number_sets_one_channel(hass: HomeAssistant, prod, lamp: FakeLamp):
    lamp.writes.clear()
    await set_value(hass, "blue", 83)
    assert lamp.channels == [22, 77, 83, 66, 44]
    assert lamp.commands[-1] == {3: 22, 4: 77, 5: 83, 6: 66, 7: 44, 14: 0}
    assert hass.states.get(NUM["blue"]).state == "83"


async def test_night_phase_all_zero_turns_off(hass: HomeAssistant, prod, lamp: FakeLamp):
    """Alt: Kanäle 0 ließen die Lampe violett glimmen, erst light.turn_off machte sie aus."""
    for channel in NUM:
        await set_value(hass, channel, 0)
    assert lamp.power is False
    assert lamp.channels == [0, 0, 0, 0, 0]
    assert hass.states.get(LIGHT).state == STATE_OFF
    await call(hass, "light", "turn_off", LIGHT)  # Skript schaltet zusätzlich aus – schadet nicht
    assert lamp.power is False


async def test_morning_channel_turns_lamp_on(hass: HomeAssistant, prod, lamp: FakeLamp):
    """Alt: number.set_value schaltete die Lampe nicht ein."""
    for channel in NUM:
        await set_value(hass, channel, 0)  # Nachtphase: aus
    await set_value(hass, "blue", 17)
    assert lamp.power is True
    assert lamp.commands[-2:] == [{2: True}, {3: 0, 4: 0, 5: 17, 6: 0, 7: 0, 14: 0}]
    assert hass.states.get(LIGHT).state == STATE_ON


async def test_channel_switches_to_manual(hass: HomeAssistant, prod, lamp: FakeLamp):
    lamp.mode = 1
    await prod.runtime_data.async_refresh()
    await set_value(hass, "cyan", 10)
    assert lamp.mode == 0
    assert lamp.commands[-2] == {1: 0}


async def test_set_channels_service_with_transition(
    hass: HomeAssistant, prod, lamp: FakeLamp, freezer: FrozenDateTimeFactory
):
    await hass.services.async_call(
        DOMAIN,
        "set_channels",
        {"entity_id": LIGHT, "pink": 0, "cyan": 0, "blue": 25, "purple": 10, "cold_white": 0},
        blocking=True,
    )
    assert lamp.channels == [0, 0, 25, 10, 0]
    await hass.services.async_call(
        DOMAIN, "set_channels", {"entity_id": LIGHT, "blue": 45, "transition": 8}, blocking=True
    )
    assert lamp.channels[2] == 30  # erster von 4 Schritten sofort
    for _ in range(4):
        await tick(hass, freezer, 2)
    assert lamp.channels == [0, 0, 45, 10, 0]


async def test_new_command_cancels_fade(hass: HomeAssistant, prod, lamp: FakeLamp, freezer: FrozenDateTimeFactory):
    await call(hass, "light", "turn_off", LIGHT, transition=20)
    await set_value(hass, "blue", 60)
    await tick(hass, freezer, 30)
    assert lamp.channels[2] == 60
    assert lamp.power is True


async def test_fade_step_failure_is_tolerated(
    hass: HomeAssistant, prod, lamp: FakeLamp, freezer: FrozenDateTimeFactory
):
    await hass.services.async_call(
        DOMAIN, "set_channels", {"entity_id": LIGHT, "blue": 40, "transition": 6}, blocking=True
    )
    assert lamp.channels[2] == 80
    lamp.ignore = 99  # alle weiteren Schritte werden verschluckt → nur Protokoll, kein Absturz
    for _ in range(4):
        await tick(hass, freezer, 2)
    assert lamp.channels[2] == 80
    lamp.ignore = 2
    with pytest.raises(HomeAssistantError, match="did not take"):
        await hass.services.async_call(
            DOMAIN, "set_channels", {"entity_id": LIGHT, "blue": 0, "transition": 6}, blocking=True
        )
    lamp.ignore = 0


# --- Licht ------------------------------------------------------------------------------------------------------


async def test_turn_on_without_color_never_white(hass: HomeAssistant, prod, lamp: FakeLamp):
    """Alt: turn_on ohne Farbe bei Kanälen 0 → 100 % Weiß-Blitz."""
    zero = dict.fromkeys(NUM, 0)
    await hass.services.async_call(DOMAIN, "set_channels", {"entity_id": LIGHT, **zero}, blocking=True)
    assert lamp.power is False
    await call(hass, "light", "turn_on", LIGHT)
    assert lamp.channels == [22, 77, 100, 66, 44]  # letzte Mischung
    assert lamp.power is True


async def test_turn_on_default_is_dim_blue(hass: HomeAssistant, registered, lamp: FakeLamp):
    lamp.power, lamp.channels = False, [0, 0, 0, 0, 0]
    assert await hass.config_entries.async_setup(registered.entry_id)
    await hass.async_block_till_done()
    await call(hass, "light", "turn_on", LIGHT)
    assert lamp.channels == [0, 0, 20, 0, 0]
    await call(hass, "light", "turn_on", LIGHT)  # schon an: nichts
    await hass.config_entries.async_unload(registered.entry_id)


async def test_turn_off_keeps_channels(hass: HomeAssistant, prod, lamp: FakeLamp):
    await call(hass, "light", "turn_off", LIGHT)
    assert lamp.power is False and lamp.channels == [22, 77, 100, 66, 44]
    assert hass.states.get(LIGHT).state == STATE_OFF
    await call(hass, "light", "turn_on", LIGHT)
    assert lamp.power is True and lamp.channels == [22, 77, 100, 66, 44]


async def test_turn_on_with_transition(hass: HomeAssistant, prod, lamp: FakeLamp, freezer: FrozenDateTimeFactory):
    await call(hass, "light", "turn_off", LIGHT)
    await call(hass, "light", "turn_on", LIGHT, transition=4)
    for _ in range(3):
        await tick(hass, freezer, 2)
    assert lamp.channels == [22, 77, 100, 66, 44]


async def test_script_restart_branch(hass: HomeAssistant, prod, lamp: FakeLamp):
    """Wiederanlauf im Skript: rgb_color [0,0,255] + Helligkeit – blau/violett, kein Weiß."""
    await call(hass, "light", "turn_off", LIGHT)
    await call(hass, "light", "turn_on", LIGHT, rgb_color=[0, 0, 255], brightness=43)
    print("RGB", lamp.channels)
    assert max(lamp.channels) == 17
    assert lamp.channels[4] <= 1  # (fast) kein Weiß
    assert lamp.power is True


async def test_rgb_keeps_brightness_and_brightness_keeps_mix(hass: HomeAssistant, prod, lamp: FakeLamp):
    await call(hass, "light", "turn_on", LIGHT, brightness=128)
    assert lamp.channels == [11, 39, 50, 33, 22]
    await call(hass, "light", "turn_on", LIGHT, rgb_color=[255, 255, 255])
    assert max(lamp.channels) == 50 and lamp.channels[4] == 50


async def test_effects(hass: HomeAssistant, prod, lamp: FakeLamp):
    await call(hass, "light", "turn_off", LIGHT)
    await call(hass, "light", "turn_on", LIGHT, effect="lightning")
    assert lamp.effect == 1 and lamp.power
    state = hass.states.get(LIGHT)
    assert state.attributes["effect"] == "lightning" and state.attributes["color_mode"] == "onoff"
    await call(hass, "light", "turn_on", LIGHT, effect="off")
    assert lamp.effect == 0 and lamp.channels == [22, 77, 100, 66, 44]
    assert hass.states.get(LIGHT).attributes["effect"] == "off"


async def test_mode_select(hass: HomeAssistant, prod, lamp: FakeLamp, freezer: FrozenDateTimeFactory):
    await call(hass, "select", "select_option", MODE, option="automatic")
    assert lamp.mode == 1
    lamp.channels = [5, 5, 5, 5, 5]  # Programm der Lampe läuft
    await tick(hass, freezer, 120)
    assert lamp.channels == [5, 5, 5, 5, 5]  # HA hält in Automatik keine Kanäle
    await call(hass, "light", "turn_off", LIGHT)
    await call(hass, "light", "turn_on", LIGHT)
    assert lamp.mode == 1 and lamp.power is True  # in Automatik nur einschalten
    await call(hass, "select", "select_option", MODE, option="manual")
    assert lamp.mode == 0


async def test_command_not_taken_is_repeated(hass: HomeAssistant, prod, lamp: FakeLamp):
    lamp.ignore = 1
    await set_value(hass, "blue", 40)
    assert lamp.channels[2] == 40
    lamp.ignore = 2
    with pytest.raises(HomeAssistantError, match="did not take"):
        await set_value(hass, "blue", 30)
    assert hass.states.get(NUM["blue"]).state == "40"  # zeigt, was die Lampe wirklich hat


# --- Zustand halten ---------------------------------------------------------------------------------------------


async def test_late_echo_is_corrected(hass: HomeAssistant, prod, lamp: FakeLamp, freezer: FrozenDateTimeFactory):
    """Vorfall: nachts Kanäle 0, dann kam Violett 10 als späte Rückmeldung zurück."""
    await hass.services.async_call(
        DOMAIN,
        "set_channels",
        {"entity_id": LIGHT, "pink": 0, "cyan": 0, "blue": 25, "purple": 0, "cold_white": 0},
        blocking=True,
    )
    await tick(hass, freezer, 10)
    lamp.push({6: 10})
    await hass.async_block_till_done()
    assert lamp.channels == [0, 0, 25, 0, 0]
    assert prod.runtime_data.stats.restores == 1
    # höchstens einmal pro Minute
    lamp.push({6: 10})
    await hass.async_block_till_done()
    assert lamp.channels[3] == 10
    await tick(hass, freezer, 61)
    assert lamp.channels[3] == 0


async def test_lamp_turned_itself_on_is_switched_off(
    hass: HomeAssistant, prod, lamp: FakeLamp, freezer: FrozenDateTimeFactory
):
    """Vorfall: Lampe ging morgens von selbst an."""
    await call(hass, "light", "turn_off", LIGHT)
    await tick(hass, freezer, 10)
    lamp.push({2: True})
    await hass.async_block_till_done()
    assert lamp.power is False


async def test_own_scene_after_power_on_is_overwritten(
    hass: HomeAssistant, prod, lamp: FakeLamp, freezer: FrozenDateTimeFactory
):
    lamp.scene_on_power = [0, 0, 0, 0, 100]
    await call(hass, "light", "turn_off", LIGHT)
    await call(hass, "light", "turn_on", LIGHT)
    assert lamp.channels == [22, 77, 100, 66, 44]


async def test_mode_changed_outside_is_restored(
    hass: HomeAssistant, prod, lamp: FakeLamp, freezer: FrozenDateTimeFactory
):
    lamp.mode = 2  # z. B. über die Fluval-App
    await tick(hass, freezer, 30)
    assert lamp.mode == 0


async def test_hold_off(hass: HomeAssistant, prod, lamp: FakeLamp, freezer: FrozenDateTimeFactory):
    await call(hass, "switch", "turn_off", f"switch.{P}_hold_state")
    assert hass.states.get(f"switch.{P}_hold_state").state == STATE_OFF
    lamp.push({6: 10})
    await tick(hass, freezer, 120)
    assert lamp.channels[3] == 10
    await call(hass, "switch", "turn_on", f"switch.{P}_hold_state")
    await tick(hass, freezer, 30)
    assert lamp.channels[3] == 66


async def test_restore_failure_is_tolerated(hass: HomeAssistant, prod, lamp: FakeLamp, freezer: FrozenDateTimeFactory):
    lamp.mode = 2
    lamp.ignore = 5
    await tick(hass, freezer, 30)
    assert prod.runtime_data.stats.restores == 1
    lamp.ignore = 0


async def test_desired_survives_restart(hass: HomeAssistant, prod, lamp: FakeLamp, freezer: FrozenDateTimeFactory):
    await call(hass, "light", "turn_off", LIGHT)
    await hass.config_entries.async_unload(prod.entry_id)
    await tick(hass, freezer, 5)  # gespeichert
    lamp.power = True  # Stromausfall: Lampe startet an
    assert await hass.config_entries.async_setup(prod.entry_id)
    await hass.async_block_till_done()
    await tick(hass, freezer, 30)
    assert lamp.power is False


# --- Knöpfe, Dienste, Diagnose ----------------------------------------------------------------------------------


async def test_buttons(hass: HomeAssistant, prod, lamp: FakeLamp):
    await call(hass, "button", "press", f"button.{P}_identify")
    assert lamp.commands[-1] == {52: "find"}
    clocks = len(lamp.clock)
    await call(hass, "button", "press", f"button.{P}_sync_clock")
    assert len(lamp.clock) == clocks + 1
    assert lamp.clock[-1][0] == 0xCD
    await call(hass, "button", "press", f"button.{P}_reconnect")
    assert lamp.connects == 2
    lamp.offline = True
    with pytest.raises(HomeAssistantError, match="Could not connect"):
        await call(hass, "button", "press", f"button.{P}_reconnect")
    with pytest.raises(HomeAssistantError, match="Could not connect"):
        await call(hass, "button", "press", f"button.{P}_identify")


async def test_get_programs(hass: HomeAssistant, prod, lamp: FakeLamp):
    result = await hass.services.async_call(
        DOMAIN, "get_programs", {"entity_id": LIGHT}, blocking=True, return_response=True
    )
    programs = result[LIGHT]
    assert programs["auto"]["sunrise"] == "07:00" and programs["auto"]["sleep"] == "22:00"
    assert programs["professional"][1] == {
        "time": "10:00",
        "pink": 22,
        "cyan": 77,
        "blue": 100,
        "purple": 66,
        "cold_white": 44,
    }
    assert programs["timed_effects"][0]["weekdays"] == [0, 1, 2, 3, 4]


async def test_diagnostics(hass: HomeAssistant, prod, lamp: FakeLamp):
    diag = await async_get_config_entry_diagnostics(hass, prod)
    assert diag["entry"]["data"]["mac"] == "**REDACTED**"
    assert diag["state"]["channels"] == [22, 77, 100, 66, 44]
    assert diag["connection"]["source"] == "esp32-bluetooth-proxy-0000a1"
    assert diag["desired"]["channels"] == [22, 77, 100, 66, 44]
    assert "übernommen" in diag["stats"]["log"][0]


async def test_on_demand_disconnects_when_idle(hass: HomeAssistant, lamp: FakeLamp, freezer: FrozenDateTimeFactory):
    entry = old_entry(active_time=120)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    c = entry.runtime_data
    assert c.link.connected
    await tick(hass, freezer, 31)
    assert not c.link.connected
    assert hass.states.get(LIGHT.replace(P, "reefnano4_0_000001")).state == STATE_ON  # Werte gelten weiter
    await tick(hass, freezer, 300)
    assert lamp.connects == 2
    await hass.config_entries.async_unload(entry.entry_id)


async def test_timed_effects_service(hass: HomeAssistant, prod, lamp):
    await hass.services.async_call(
        DOMAIN,
        "set_timed_effects",
        {
            "entity_id": LIGHT,
            "windows": [
                {"start": "21:30", "end": "22:00", "effect": "crescent_moon"},
                {"start": "12:00", "end": "12:10", "effect": "lightning", "weekdays": ["sat", "sun"], "enabled": False},
            ],
        },
        blocking=True,
    )
    blob = lamp.effect_schedule
    assert len(blob) == 42
    assert list(blob[:6]) == [0x80 | 0x7F, 21, 30, 22, 0, 4]  # jeden Tag, Mondsichel
    assert list(blob[6:12]) == [0b1000001, 12, 0, 12, 10, 1]  # Sa (Bit 6) + So (Bit 0), aus
    assert blob[12:] == bytes(30)
    sensor = next(s for s in hass.states.async_all("sensor") if s.entity_id.endswith("_timed_effects"))
    assert sensor.attributes["windows"][0]["effect"] == "crescent_moon"
    await hass.services.async_call(DOMAIN, "set_timed_effects", {"entity_id": LIGHT, "windows": []}, blocking=True)
    assert lamp.effect_schedule == bytes(42)


async def test_weather_effects(hass: HomeAssistant, prod, lamp, freezer):
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    async def tick(seconds: float) -> None:
        freezer.tick(seconds)
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()

    hass.states.async_set("weather.wetterstation", "cloudy", {})
    result = await hass.config_entries.options.async_init(prod.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"keep_connected": True, "weather_entity": "weather.wetterstation"}
    )
    await hass.async_block_till_done()
    select = f"select.{P}_weather_effects"
    await hass.services.async_call("select", "select_option", {"entity_id": select, "option": "storm"}, blocking=True)
    await hass.services.async_call(
        "number", "set_value", {"entity_id": f"number.{P}_weather_effect_minutes", "value": 5}, blocking=True
    )
    await hass.async_block_till_done()
    assert lamp.effect == 0  # nur Wolken, Modus „nur Gewitter“
    hass.states.async_set("weather.wetterstation", "lightning-rainy", {})
    await hass.async_block_till_done()
    assert lamp.effect == 1
    await tick(5 * 60 + 1)
    assert lamp.effect == 0 and lamp.channels == [22, 77, 100, 66, 44]  # zurück zur Mischung
    # höchstens einmal pro Stunde
    hass.states.async_set("weather.wetterstation", "cloudy", {})
    hass.states.async_set("weather.wetterstation", "lightning", {})
    await hass.async_block_till_done()
    assert lamp.effect == 0
    await tick(3600)
    hass.states.async_set("weather.wetterstation", "partlycloudy", {})
    await hass.services.async_call(
        "select", "select_option", {"entity_id": select, "option": "storm_clouds"}, blocking=True
    )
    await hass.async_block_till_done()
    assert lamp.effect == 3  # Wolken-Effekt
    # Licht aus → kein Wetter-Effekt
    await tick(3600 + 5 * 60)
    await hass.services.async_call("light", "turn_off", {"entity_id": LIGHT}, blocking=True)
    hass.states.async_set("weather.wetterstation", "lightning", {})
    await hass.async_block_till_done()
    assert lamp.effect == 0


async def test_weather_effect_start_failure_resets(hass: HomeAssistant, prod, lamp):
    """Scheitert der Effekt-Befehl, wird der Wetter-Effekt verworfen (sonst bliebe er als „läuft“ hängen)."""
    from custom_components.fluvalble.coordinator import FluvalCoordinator

    hass.states.async_set("weather.wetterstation", "cloudy", {})
    result = await hass.config_entries.options.async_init(prod.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"keep_connected": True, "weather_entity": "weather.wetterstation"}
    )
    await hass.async_block_till_done()
    await hass.services.async_call(
        "select", "select_option", {"entity_id": f"select.{P}_weather_effects", "option": "storm"}, blocking=True
    )
    coordinator = prod.runtime_data
    with (
        patch.object(FluvalCoordinator, "async_set_effect", side_effect=HomeAssistantError("Lampe weg")),
        patch("custom_components.fluvalble.coordinator._LOGGER.warning") as warning,
    ):
        hass.states.async_set("weather.wetterstation", "lightning", {})
        await hass.async_block_till_done()
    assert coordinator._fx_effect == 0 and coordinator._fx_cancel is None
    assert warning.call_count == 1 and "Wetter-Effekt nicht gestartet" in warning.call_args.args[0]
    assert lamp.effect == 0


async def test_set_pro_program(hass: HomeAssistant, prod, lamp):
    day = {"pink": 22, "cyan": 77, "blue": 100, "purple": 66, "cold_white": 44}
    await hass.services.async_call(
        DOMAIN,
        "set_pro_program",
        {
            "entity_id": LIGHT,
            "points": [
                {"time": "22:00"},
                {"time": "09:00"},
                {"time": "10:00", **day},
                {"time": "19:40", **day},
                {"time": "20:00", "blue": 25, "purple": 10},
            ],
        },
        blocking=True,
    )
    assert lamp.pro == bytes(
        (
            5,
            9,
            0,
            0,
            0,
            0,
            0,
            0,
            10,
            0,
            22,
            77,
            100,
            66,
            44,
            19,
            40,
            22,
            77,
            100,
            66,
            44,
            20,
            0,
            0,
            0,
            25,
            10,
            0,
            22,
            0,
            0,
            0,
            0,
            0,
            0,
        )
    )
    assert lamp.mode == 2  # professional
    res = await hass.services.async_call(
        DOMAIN, "get_programs", {"entity_id": LIGHT}, blocking=True, return_response=True
    )
    assert res[LIGHT]["professional"][1] == {"time": "10:00", **day}
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            "set_pro_program",
            {"entity_id": LIGHT, "points": [{"time": "9:00"}, {"time": "9:00"}]},
            blocking=True,
        )


async def test_late_confirmation_after_program(hass: HomeAssistant, prod, lamp):
    """Nach dem Speichern eines Programms antwortet die Lampe erst auf die zweite Abfrage – kein Fehler."""
    lamp.late = 1
    await hass.services.async_call(
        DOMAIN,
        "set_pro_program",
        {"entity_id": LIGHT, "points": [{"time": "09:00"}, {"time": "10:00", "blue": 50}], "activate": False},
        blocking=True,
    )
    assert lamp.pro is not None and lamp.pro[0] == 2


async def test_pro_mode_shows_program(hass: HomeAssistant, prod, lamp, freezer: FrozenDateTimeFactory):
    """Im Profi-Modus zeigt das Licht, was das Programm gerade spielt."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to("2030-10-07 07:30:00+00:00")  # 09:30 Ortszeit, nach der echten Uhr (Minutentakt fällig)
    await hass.services.async_call(
        DOMAIN,
        "set_pro_program",
        {
            "entity_id": LIGHT,
            "points": [{"time": "09:00"}, {"time": "10:00", "blue": 60, "cyan": 40}, {"time": "19:00"}],
        },
        blocking=True,
    )
    state = hass.states.get(LIGHT)
    assert state.state == "on" and state.attributes["channels"]["blue"] == 30
    assert state.attributes["channels"]["cyan"] == 20
    # Die Regler zeigen dasselbe wie das Licht, nicht die alte Handmischung
    assert hass.states.get(NUM["blue"]).state == "30" and hass.states.get(NUM["cyan"]).state == "20"
    await tick(hass, freezer, 60 * 60 * 10 + 5)  # 19:30:05 → nach dem letzten Punkt dunkel
    assert hass.states.get(LIGHT).state == "off"
    assert hass.states.get(NUM["blue"]).state == "0"


async def test_slider_leaves_pro_mode_from_shown_levels(
    hass: HomeAssistant, prod, lamp, freezer: FrozenDateTimeFactory
):
    """Regler im Profi-Modus bewegen: Hand-Modus, die übrigen Kanäle übernehmen den angezeigten Stand."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to("2030-10-07 07:30:00+00:00")  # 09:30 Ortszeit
    await hass.services.async_call(
        DOMAIN,
        "set_pro_program",
        {
            "entity_id": LIGHT,
            "points": [{"time": "09:00"}, {"time": "10:00", "blue": 60, "cyan": 40}, {"time": "19:00"}],
        },
        blocking=True,
    )
    await hass.services.async_call("number", "set_value", {"entity_id": NUM["blue"], "value": 50}, blocking=True)
    assert lamp.mode == 0 and lamp.channels == [0, 20, 50, 0, 0]
    assert hass.states.get(NUM["cyan"]).state == "20"
