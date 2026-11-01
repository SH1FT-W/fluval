"""Saved profiles: button saves the lamp's professional program, select writes one back, options rename/delete."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fluvalble import profiles
from custom_components.fluvalble.const import DOMAIN, PROFILE_NAME_MAX, PROFILES_MAX
from custom_components.fluvalble.protocol import ProPoint

from .conftest import ENTRY_ID, MAC, FakeLamp

PREFIX = "reefnano4_0_000001"
SELECT = f"select.{PREFIX}_profile"
SAVE = f"button.{PREFIX}_save_profile"
# Program of the fake lamp (conftest): 09:00 dark, 10:00 day mix, 20:00 evening, 22:00 dark.
LAMP_POINTS = [[540, 0, 0, 0, 0, 0], [600, 22, 77, 100, 66, 44], [1200, 25, 0, 25, 10, 0], [1320, 0, 0, 0, 0, 0]]
OTHER_POINTS = [[480, 0, 0, 0, 0, 0], [720, 10, 20, 30, 40, 50]]


def entry(**options: Any) -> MockConfigEntry:
    """Current entry version (2.2), so the options are taken as they are."""
    return MockConfigEntry(
        domain=DOMAIN,
        entry_id=ENTRY_ID,
        title="ReefNano4.0_000001",
        version=2,
        minor_version=2,
        unique_id=MAC,
        data={"mac": MAC, "model": "Fluval Reef Nano 4.0 LED", "product_id": 547},
        options={"keep_connected": True, **options},
    )


@pytest.fixture
async def loaded(hass: HomeAssistant, lamp: FakeLamp, request):
    e = entry(**getattr(request, "param", {}))
    e.add_to_hass(hass)
    assert await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    yield e
    if e.state.recoverable:
        await hass.config_entries.async_unload(e.entry_id)
        await hass.async_block_till_done()


async def set_mode(hass: HomeAssistant, mode: str) -> None:
    await hass.services.async_call(
        "select", "select_option", {"entity_id": f"select.{PREFIX}_mode", "option": mode}, blocking=True
    )
    await hass.async_block_till_done()


async def press(hass: HomeAssistant) -> None:
    await hass.services.async_call("button", "press", {"entity_id": SAVE}, blocking=True)
    await hass.async_block_till_done()


async def select(hass: HomeAssistant, option: str) -> None:
    await hass.services.async_call("select", "select_option", {"entity_id": SELECT, "option": option}, blocking=True)
    await hass.async_block_till_done()


def saved(e: MockConfigEntry) -> dict[str, list]:
    return {p["name"]: p["points"] for p in e.options.get("profiles", [])}


async def test_save_and_apply(hass: HomeAssistant, loaded, lamp: FakeLamp):
    state = hass.states.get(SELECT)
    assert state.state == STATE_UNKNOWN and state.attributes["options"] == []

    await set_mode(hass, "professional")  # the select shows the profile only while it runs
    coordinator, connects = loaded.runtime_data, lamp.connects
    await press(hass)
    assert saved(loaded) == {"Profil 1": LAMP_POINTS}
    # Saving changes the options but must not reload the entry (that would drop the Bluetooth connection).
    assert loaded.runtime_data is coordinator and lamp.connects == connects
    state = hass.states.get(SELECT)
    assert state.state == "Profil 1" and state.attributes["options"] == ["Profil 1"]
    assert loaded.options["keep_connected"] is True  # other options stay

    # Same program again: nothing new, the user learns under which name it is saved.
    with pytest.raises(HomeAssistantError, match="already saved as profile Profil 1"):
        await press(hass)

    # Another program picked in the app → the select no longer matches, the button saves it as profile 2.
    lamp.pro = bytes((2, 8, 0, 0, 0, 0, 0, 0, 12, 0, 10, 20, 30, 40, 50))
    await press(hass)
    assert saved(loaded) == {"Profil 1": LAMP_POINTS, "Profil 2": OTHER_POINTS}
    assert hass.states.get(SELECT).state == "Profil 2"

    # Picking profile 1 writes it into the lamp and switches to professional.
    lamp.mode = 0
    await select(hass, "Profil 1")
    assert lamp.pro == bytes(
        (4, 9, 0, 0, 0, 0, 0, 0, 10, 0, 22, 77, 100, 66, 44, 20, 0, 25, 0, 25, 10, 0, 22, 0, 0, 0, 0, 0, 0)
    )
    assert lamp.mode == 2
    assert hass.states.get(SELECT).state == "Profil 1"
    assert hass.states.get(f"select.{PREFIX}_mode").state == "professional"


async def test_save_needs_program_and_lamp(hass: HomeAssistant, loaded, lamp: FakeLamp):
    lamp.pro = bytes((0,))
    with pytest.raises(HomeAssistantError, match="holds no professional program"):
        await press(hass)
    lamp.offline = True
    await loaded.runtime_data.link.disconnect()
    with pytest.raises(HomeAssistantError, match="Could not connect"):
        await loaded.runtime_data.async_save_profile()
    assert "profiles" not in loaded.options


@pytest.mark.parametrize(
    "loaded",
    [{"profiles": [{"name": f"Profil {n}", "points": [[n, 1, 1, 1, 1, 1]]} for n in range(1, PROFILES_MAX + 1)]}],
    indirect=True,
)
async def test_save_limit(hass: HomeAssistant, loaded):
    with pytest.raises(HomeAssistantError, match=f"At most {PROFILES_MAX} profiles"):
        await press(hass)


@pytest.mark.parametrize("loaded", [{"profiles": [{"name": "Profil 2", "points": OTHER_POINTS}]}], indirect=True)
async def test_new_name_skips_taken(hass: HomeAssistant, loaded):
    await press(hass)
    assert list(saved(loaded)) == ["Profil 2", "Profil 3"]


async def test_unknown_profile(hass: HomeAssistant, loaded):
    with pytest.raises(HomeAssistantError, match="no saved profile named Urlaub"):
        await loaded.runtime_data.async_apply_profile("Urlaub")


def test_load_skips_broken_entries():
    good = {"name": " Normal ", "points": [[600, 1, 2, 3, 4, 5], [540, 0, 0, 0, 0, 0]]}
    raw = [
        good,
        {"name": "Normal", "points": OTHER_POINTS},  # duplicate name
        {"name": "", "points": OTHER_POINTS},
        {"name": "x", "points": []},
        {"name": "x", "points": [[1440, 0, 0, 0, 0, 0]]},
        {"name": "x", "points": [[600, 0, 0, 0, 0]]},
        {"name": "x", "points": [[600, True, 0, 0, 0, 0]]},
        {"name": "x", "points": "09:00"},
        "Normal",
    ]
    result = profiles.load({"profiles": raw})
    assert result == [profiles.Profile("Normal", (ProPoint(540, (0, 0, 0, 0, 0)), ProPoint(600, (1, 2, 3, 4, 5))))]
    assert profiles.describe(result[0]) == "09:00 0/0/0/0/0 · 10:00 1/2/3/4/5"
    assert profiles.load({"profiles": "nope"}) == [] and profiles.load({}) == []


# --- Options ------------------------------------------------------------------------------------------------

TWO = {"profiles": [{"name": "Normal", "points": LAMP_POINTS}, {"name": "Urlaub", "points": OTHER_POINTS}]}


async def open_profile(hass: HomeAssistant, e: MockConfigEntry, name: str) -> dict[str, Any]:
    result = await hass.config_entries.options.async_init(e.entry_id)
    assert result["type"] is FlowResultType.MENU and result["menu_options"] == ["settings", "profiles"]
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "profiles"})
    assert result["step_id"] == "profiles"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"profiles": name})
    assert result["step_id"] == "profile"
    return result


@pytest.mark.parametrize("loaded", [TWO], indirect=True)
async def test_options_rename(hass: HomeAssistant, loaded):
    result = await open_profile(hass, loaded, "Urlaub")
    assert result["description_placeholders"]["points"] == "08:00 0/0/0/0/0 · 12:00 10/20/30/40/50"
    flow = result["flow_id"]
    result = await hass.config_entries.options.async_configure(flow, {"name": "Normal", "delete": False})
    assert result["errors"] == {"name": "name_taken"}
    result = await hass.config_entries.options.async_configure(flow, {"name": "  ", "delete": False})
    assert result["errors"] == {"name": "name_empty"}
    result = await hass.config_entries.options.async_configure(flow, {"name": " Ferien ", "delete": False})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert saved(loaded) == {"Normal": LAMP_POINTS, "Ferien": OTHER_POINTS}
    assert loaded.options["keep_connected"] is True
    assert hass.states.get(SELECT).attributes["options"] == ["Normal", "Ferien"]
    await set_mode(hass, "professional")
    assert hass.states.get(SELECT).state == "Normal"


@pytest.mark.parametrize("loaded", [TWO], indirect=True)
async def test_options_delete_all(hass: HomeAssistant, loaded):
    for name in ("Normal", "Urlaub"):
        result = await open_profile(hass, loaded, name)
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"name": name, "delete": True})
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
    assert loaded.options == {"keep_connected": True}
    # Without profiles the options open the settings directly, as before.
    result = await hass.config_entries.options.async_init(loaded.entry_id)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "settings"


@pytest.mark.parametrize("loaded", [TWO], indirect=True)
async def test_options_settings_keep_profiles(hass: HomeAssistant, loaded):
    result = await hass.config_entries.options.async_init(loaded.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "settings"})
    assert result["step_id"] == "settings"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"keep_connected": False})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert loaded.options["keep_connected"] is False
    assert saved(loaded) == {"Normal": LAMP_POINTS, "Urlaub": OTHER_POINTS}


@pytest.mark.parametrize("loaded", [TWO], indirect=True)
async def test_options_profile_gone(hass: HomeAssistant, loaded):
    result = await open_profile(hass, loaded, "Urlaub")
    hass.config_entries.async_update_entry(loaded, options={"keep_connected": True, "profiles": TWO["profiles"][:1]})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"name": "x", "delete": False})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "profile_gone"


# --- Findings of the adversarial review -----------------------------------------------------------------------


@pytest.mark.parametrize("loaded", [{"profiles": [{"name": "Keep", "points": OTHER_POINTS}]}], indirect=True)
async def test_save_uses_fresh_list(hass: HomeAssistant, loaded):
    """A profile deleted while the button waits (translations not cached yet) must stay deleted."""

    async def slow_translations(*_args: Any) -> dict[str, str]:
        hass.config_entries.async_update_entry(loaded, options={"keep_connected": True})
        return {}

    with patch("custom_components.fluvalble.coordinator.async_get_translations", side_effect=slow_translations):
        await loaded.runtime_data.async_save_profile()
    assert list(saved(loaded)) == ["Profile 1"]


async def test_save_rejects_duplicate_times(hass: HomeAssistant, loaded, lamp: FakeLamp):
    lamp.pro = bytes((2, 9, 0, 1, 1, 1, 1, 1, 9, 0, 2, 2, 2, 2, 2))
    with pytest.raises(HomeAssistantError, match="Invalid professional program"):
        await press(hass)
    assert "profiles" not in loaded.options
    assert profiles.load({"profiles": [{"name": "x", "points": [[540, 1, 1, 1, 1, 1], [540, 2, 2, 2, 2, 2]]}]}) == []


async def test_save_ignores_unreadable_program(hass: HomeAssistant, loaded, lamp: FakeLamp):
    """If the lamp's new program cannot be decoded, the old one must not be saved as if it were fresh."""
    lamp.pro = bytes((13,)) + bytes(13 * 7)
    with pytest.raises(HomeAssistantError, match="no readable professional program"):
        await press(hass)
    assert "profiles" not in loaded.options


async def test_save_error_in_diagnostics(hass: HomeAssistant, loaded, lamp: FakeLamp):
    lamp.offline = True
    await loaded.runtime_data.link.disconnect()
    with pytest.raises(HomeAssistantError):
        await press(hass)
    assert loaded.runtime_data.stats.last_error


async def test_select_only_active_in_professional(hass: HomeAssistant, loaded, lamp: FakeLamp):
    await press(hass)
    assert hass.states.get(SELECT).state == STATE_UNKNOWN  # lamp in manual: program stored, not running
    await select(hass, "Profil 1")
    assert hass.states.get(SELECT).state == "Profil 1"


@pytest.mark.parametrize("loaded", [TWO], indirect=True)
async def test_options_name_length(hass: HomeAssistant, loaded):
    result = await open_profile(hass, loaded, "Urlaub")
    long = "x" * (PROFILE_NAME_MAX + 1)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"name": long, "delete": False})
    assert result["errors"] == {"name": "name_too_long"}
    assert profiles.load({"profiles": [{"name": long, "points": OTHER_POINTS}]}) == []


async def test_save_needs_program_in_this_answer(hass: HomeAssistant, loaded, lamp: FakeLamp):
    """An answer without key 13 must not save the program of an earlier answer."""
    full = lamp.full_status
    lamp.full_status = lambda: {k: v for k, v in full().items() if k != 13}
    lamp.pro = bytes((2, 8, 0, 0, 0, 0, 0, 0, 12, 0, 10, 20, 30, 40, 50))
    with pytest.raises(HomeAssistantError, match="no readable professional program"):
        await press(hass)
    assert "profiles" not in loaded.options


async def test_select_unknown_when_program_unreadable(hass: HomeAssistant, loaded, lamp: FakeLamp):
    await set_mode(hass, "professional")
    await press(hass)
    assert hass.states.get(SELECT).state == "Profil 1"
    lamp.pro = bytes((13,)) + bytes(13 * 7)  # picked in the app, but not readable for Home Assistant
    await loaded.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(SELECT).state == STATE_UNKNOWN


@pytest.mark.parametrize("loaded", [TWO], indirect=True)
async def test_options_profiles_emptied_meanwhile(hass: HomeAssistant, loaded):
    result = await hass.config_entries.options.async_init(loaded.entry_id)
    hass.config_entries.async_update_entry(loaded, options={"keep_connected": True})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "profiles"})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "profile_gone"


@pytest.mark.parametrize(
    "loaded", [{"profiles": [{"name": "Gut", "points": OTHER_POINTS}, {"name": "Kaputt", "points": []}]}], indirect=True
)
async def test_broken_profiles_logged_on_start(hass: HomeAssistant, loaded):
    assert [p.name for p in loaded.runtime_data.profiles] == ["Gut"]
    assert any("1 ungültige" in line for line in loaded.runtime_data.stats.log)


FUTURE = {"name": "Zukunft", "points": [[600, 1, 2, 3, 4, 5, 6]]}  # e.g. written by a later version


@pytest.mark.parametrize("loaded", [{"profiles": [{"name": "Gut", "points": OTHER_POINTS}, FUTURE]}], indirect=True)
async def test_unreadable_entries_survive_saving(hass: HomeAssistant, loaded):
    await press(hass)
    assert list(saved(loaded)) == ["Gut", "Profil 2", "Zukunft"]
    assert loaded.options["profiles"][-1] == FUTURE
    result = await open_profile(hass, loaded, "Gut")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"name": "Gut", "delete": True})
    await hass.async_block_till_done()
    assert list(saved(loaded)) == ["Profil 2", "Zukunft"]


@pytest.mark.parametrize("loaded", [{"profiles": [FUTURE]}], indirect=True)
async def test_only_unreadable_entries_kept_by_settings(hass: HomeAssistant, loaded):
    result = await hass.config_entries.options.async_init(loaded.entry_id)
    assert result["step_id"] == "settings"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"keep_connected": False})
    await hass.async_block_till_done()
    assert loaded.options["profiles"] == [FUTURE]
