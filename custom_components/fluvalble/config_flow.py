"""Einrichtung über Bluetooth: die Lampe wird gefunden (Werbedaten) oder aus der Liste gewählt."""

from __future__ import annotations

from typing import Any

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.core import callback
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)
from homeassistant.helpers.translation import async_get_translations
import voluptuous as vol

from . import profiles as profile_store
from .const import (
    CONF_KEEP_CONNECTED,
    CONF_MAC,
    CONF_MODEL,
    CONF_PREFERRED_PROXY,
    CONF_PRODUCT_ID,
    CONF_PROFILES,
    CONF_WEATHER,
    DEFAULT_KEEP_CONNECTED,
    DOMAIN,
    PROFILE_NAME_MAX,
    PROXY_AUTO,
)
from .products import DEFAULT_PRODUCT, Product, product_for, product_id_from_advertisement


def product_id(info: bluetooth.BluetoothServiceInfoBleak) -> int | None:
    """Product ID from the advertisement (like the app: company FFFF/FF01, big-endian ID at offset 8–9)."""
    return product_id_from_advertisement(info.manufacturer_data)


def detect(info: bluetooth.BluetoothServiceInfoBleak) -> Product | None:
    """Supported model of this advertisement, or None (old generation, other devices)."""
    if (product := product_for(product_id(info))) is not None:
        return product
    if product_id(info) is None and (info.name or "").replace(" ", "").lower().startswith("reefnano"):
        return DEFAULT_PRODUCT
    return None


def entry_data(info: bluetooth.BluetoothServiceInfoBleak) -> dict[str, Any]:
    """Wie die alte Integration den Eintrag gespeichert hat."""
    product = detect(info) or DEFAULT_PRODUCT
    return {
        CONF_MAC: info.address.upper(),
        CONF_MODEL: product.model,
        CONF_PRODUCT_ID: product.product_id,
        "manufacturer_data": {str(k): v.hex() for k, v in info.manufacturer_data.items()},
        "service_data": {k: v.hex() for k, v in info.service_data.items()},
        "service_uuids": list(info.service_uuids),
    }


class FluvalConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 2
    MINOR_VERSION = 2

    def __init__(self) -> None:
        self._info: bluetooth.BluetoothServiceInfoBleak | None = None
        self._found: dict[str, bluetooth.BluetoothServiceInfoBleak] = {}

    async def async_step_bluetooth(self, discovery_info: bluetooth.BluetoothServiceInfoBleak) -> ConfigFlowResult:
        if detect(discovery_info) is None:
            return self.async_abort(reason="not_supported")
        await self.async_set_unique_id(format_mac(discovery_info.address))
        self._abort_if_unique_id_configured()
        self._info = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name or discovery_info.address}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._info is not None
        if user_input is not None:
            return self._create(self._info)
        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm", description_placeholders={"name": self._info.name or self._info.address}
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            info = self._found[user_input[CONF_MAC]]
            await self.async_set_unique_id(format_mac(info.address), raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return self._create(info)
        configured = self._async_current_ids(include_ignore=False)
        self._found = {
            info.address: info
            for info in bluetooth.async_discovered_service_info(self.hass, connectable=True)
            if detect(info) is not None and format_mac(info.address) not in configured
        }
        if not self._found:
            return self.async_abort(reason="no_devices_found")
        names = {
            address: f"{info.name or (detect(info) or DEFAULT_PRODUCT).model} ({address})"
            for address, info in self._found.items()
        }
        return self.async_show_form(step_id="user", data_schema=vol.Schema({vol.Required(CONF_MAC): vol.In(names)}))

    def _create(self, info: bluetooth.BluetoothServiceInfoBleak) -> ConfigFlowResult:
        return self.async_create_entry(
            title=info.name or (detect(info) or DEFAULT_PRODUCT).model,
            data=entry_data(info),
            options={CONF_KEEP_CONNECTED: DEFAULT_KEEP_CONNECTED},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> FluvalOptionsFlow:
        return FluvalOptionsFlow()


class FluvalOptionsFlow(OptionsFlowWithReload):
    """Settings; with saved profiles first a menu: settings or rename/delete a profile."""

    def __init__(self) -> None:
        self._profile: str | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if profile_store.load(self.config_entry.options):
            return self.async_show_menu(step_id="init", menu_options=["settings", "profiles"])
        return await self.async_step_settings()

    def _save(self, settings: dict[str, Any], profiles: list[profile_store.Profile]) -> ConfigFlowResult:
        """Options = settings plus the profiles (left out when there are none)."""
        data = dict(settings)
        if profiles:
            data[CONF_PROFILES] = profile_store.dump(profiles)
        return self.async_create_entry(data=data)

    def _settings(self) -> dict[str, Any]:
        return {k: v for k, v in self.config_entry.options.items() if k != CONF_PROFILES}

    async def async_step_profiles(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick the profile to rename or delete."""
        if user_input is not None:
            self._profile = user_input[CONF_PROFILES]
            return await self.async_step_profile()
        names = [p.name for p in profile_store.load(self.config_entry.options)]
        if not names:  # emptied meanwhile (another options dialog or the options changed from outside)
            return self.async_abort(reason="profile_gone")
        schema = vol.Schema(
            {
                vol.Required(CONF_PROFILES, default=names[0]): SelectSelector(
                    SelectSelectorConfig(options=names, mode=SelectSelectorMode.LIST)
                )
            }
        )
        return self.async_show_form(step_id="profiles", data_schema=schema)

    async def async_step_profile(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Rename or delete one profile; the points come from the lamp (button "Save program as profile")."""
        saved = profile_store.load(self.config_entry.options)
        profile = next((p for p in saved if p.name == self._profile), None)
        if profile is None:  # changed in the meantime (e.g. the button saved another profile)
            return self.async_abort(reason="profile_gone")
        errors: dict[str, str] = {}
        if user_input is not None:
            others = [p for p in saved if p is not profile]
            if user_input.get("delete"):
                return self._save(self._settings(), others)
            name = str(user_input.get("name", "")).strip()
            if not name:
                errors["name"] = "name_empty"
            elif len(name) > PROFILE_NAME_MAX:
                errors["name"] = "name_too_long"
            elif any(p.name == name for p in others):
                errors["name"] = "name_taken"
            else:
                renamed = profile_store.Profile(name, profile.points)
                return self._save(self._settings(), [renamed if p is profile else p for p in saved])
        schema = vol.Schema(
            {
                vol.Required("name", default=profile.name): TextSelector(),
                vol.Required("delete", default=False): bool,
            }
        )
        return self.async_show_form(
            step_id="profile",
            data_schema=schema,
            errors=errors,
            description_placeholders={"name": profile.name, "points": profile_store.describe(profile)},
        )

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self._save(user_input, profile_store.load(self.config_entry.options))
        options = self.config_entry.options
        current = options.get(CONF_KEEP_CONNECTED, DEFAULT_KEEP_CONNECTED)
        preferred = options.get(CONF_PREFERRED_PROXY, PROXY_AUTO)
        schema = vol.Schema(
            {
                vol.Required(CONF_KEEP_CONNECTED, default=current): bool,
                vol.Required(CONF_PREFERRED_PROXY, default=preferred): SelectSelector(
                    SelectSelectorConfig(
                        options=await self._proxy_options(preferred),
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key=CONF_PREFERRED_PROXY,
                    )
                ),
                vol.Optional(CONF_WEATHER, description={"suggested_value": options.get(CONF_WEATHER)}): EntitySelector(
                    EntitySelectorConfig(domain="weather")
                ),
            }
        )
        return self.async_show_form(step_id="settings", data_schema=schema)

    async def _proxy_options(self, preferred: str) -> list[SelectOptionDict]:
        """ "Automatic" plus every proxy/adapter that can connect (signal if it sees the lamp), translated labels."""
        texts = await async_get_translations(self.hass, self.hass.config.language, "selector", {DOMAIN})
        prefix = f"component.{DOMAIN}.selector.{CONF_PREFERRED_PROXY}.options."
        address = self.config_entry.data[CONF_MAC].upper()
        rssi = {
            found.scanner.source: found.advertisement.rssi
            for found in bluetooth.async_scanner_devices_by_address(self.hass, address, connectable=True)
        }
        options = [SelectOptionDict(value=PROXY_AUTO, label=texts.get(prefix + PROXY_AUTO, "Automatic"))]
        known = {PROXY_AUTO}
        for scanner in sorted(bluetooth.async_current_scanners(self.hass), key=lambda s: s.name):
            if not scanner.connectable or scanner.source in known:
                continue
            known.add(scanner.source)
            label = scanner.name if scanner.source not in rssi else f"{scanner.name} ({rssi[scanner.source]} dBm)"
            options.append(SelectOptionDict(value=scanner.source, label=label))
        if preferred not in known:
            missing = texts.get(prefix + "missing", "not seen")
            options.append(SelectOptionDict(value=preferred, label=f"{preferred} ({missing})"))
        return options
