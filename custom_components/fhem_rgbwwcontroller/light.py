from typing import Any, cast

import voluptuous as vol

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ATTR_RGBWW_COLOR,
    DEFAULT_MAX_KELVIN,
    DEFAULT_MIN_KELVIN,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import entity_platform
from homeassistant.util.color import (
    color_hs_to_xy,
    color_temperature_kelvin_to_mired,
    color_temperature_mired_to_kelvin,
)


# Import the device class from the component that you want to support
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .rgbww_controller import RgbwwController

SERVICE_SET_HSV_ADV = "set_hsv_advanced"
SERVICE_PAUSE = "PAUSE"
SERVICE_CONTINUE = "CONTINUE"
SERVICE_SKIP = "SKIP"
SERVICE_STOP = "STOP"


def _service_set_hsv_advanced(self, call: ServiceCall) -> None:
    print("hhlo")
    if call.data["hsv_command_string"] == "fade":
        print("as")


def _service_pause(self, call: ServiceCall) -> None:
    if call.data["hsv_command_string"] == "fade":
        print("as")


def _service_continue(self, call: ServiceCall) -> None:
    if call.data["hsv_command_string"] == "fade":
        print("as")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Abode light devices."""
    controller = cast(RgbwwController, entry.runtime_data)

    rgb = RgbwwLight(
        hass,
        controller,
        entry,
    )

    async_add_entities((rgb,))

    # Register the service to set HSV with advanced options
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SET_HSV_ADV,
        {vol.Required("hsv_command_string"): cv.string},
        _service_set_hsv_advanced,
    )

    platform.async_register_entity_service(
        SERVICE_PAUSE,
        vol.Schema(
            {
                vol.Required("all_channels"): bool,
                vol.Optional("channel_h"): bool,
                vol.Optional("channel_s"): bool,
                vol.Optional("channel_v"): bool,
            }
        ),
        _service_pause,
    )


# we implement RgbwwStateUpdate but we cannot derive from here due to metaclass error
class RgbwwLight(LightEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False

    _attr_max_color_temp_kelvin = DEFAULT_MAX_KELVIN
    _attr_min_color_temp_kelvin = DEFAULT_MIN_KELVIN

    def __init__(
        self,
        hass: HomeAssistant,
        controller: RgbwwController,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the light."""
        super().__init__()

        self._config_entry = entry
        self._controller = controller

        self._first_connection = True
        self._hass = hass
        #if unique_id is not None:
        #    self._attr_unique_id = unique_id + "_light"
        self._attr_name = entry.title + " Light"

        self._attr_supported_color_modes = (
            ColorMode.HS,
            # ColorMode.RGBWW,
            ColorMode.COLOR_TEMP,
        )
        self._attr_supported_features = (
            LightEntityFeature.TRANSITION
            | LightEntityFeature.FLASH
            | LightEntityFeature.EFFECT
        )
        self._attr_color_mode = {ColorMode.HS}


    async def async_added_to_hass(self) -> None:
        self._controller.register_callback(self)

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from the events."""
        self._controller.unregister_callback(self)

        await super().async_will_remove_from_hass()

    def on_update_hsv(self, h: int | None, s: int | None, v: int | None) -> None:
        if h is not None:
            self._attr_hs_color = (h, s)

        if v is not None:
            self._attr_brightness = (v / 100.0) * 255

        self._attr_is_on = v > 0

        self.async_write_ha_state()

    # protocol rgbww state
    async def on_avilability_update(self, connected: bool) -> None:
        self._attr_available = connected

        if connected and self._first_connection:
            self._first_connection = False

        #self._attr_color_temp_kelvin = self._controller.color.color_temp
        #self._attr_max_color_temp_kelvin = self._controller.config["color"][
        #    "colortemp"
        #]["cw"]
        #self._attr_min_color_temp_kelvin = self._controller.config["color"][
        #    "colortemp"
        #]["ww"]

            # --- ENTITY AND DEVICE LINKING ---

        #sw_version=f"{controller.info['git_version']} (WebApp:{controller.info['webapp_version']})",

            # This `device_info` block links the entity to the device
            # in __init__.py. The `identifiers` MUST match exactly.
            await self._controller.refresh()

            self._attr_device_info = DeviceInfo(
                config_entry_id=self._config_entry.entry_id,
                identifiers={(DOMAIN, self._config_entry.unique_id)},
                name=self._config_entry.title,  # The name the user gave in the config flow
                manufacturer="Homebrew Hardware",
                model="FHEM RGBWW LED Controller",  # Replace with actual model
                sw_version=f"{self._controller.info['git_version']} (WebApp:{self._controller.info['webapp_version']})",
            )
            # --- END LINKING ---
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        if (rgbww := kwargs.get(ATTR_RGBWW_COLOR)) is not None:
            await self._controller.set_raw(*rgbww)
        elif (hs := kwargs.get(ATTR_HS_COLOR)) is not None:
            self._attr_color_mode = ColorMode.HS
            args = {"hue": hs[0], "saturation": hs[1]}
            if "transition" in kwargs:
                args["t"] = float(kwargs["transition"])
            await self._controller.set_hsv(**args)
        elif (ct := kwargs.get(ATTR_COLOR_TEMP_KELVIN)) is not None:
            await self._controller.set_hsv(ct=ct)
        elif (rgbww := kwargs.get(ATTR_RGBWW_COLOR)) is not None:
            self._attr_color_mode = ColorMode.RGBWW
            # Call your API's raw mode function
        elif (brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None:
            await self._controller.set_hsv(brightness=(brightness / 255.0) * 100)
        else:  # Turn on with last known state or default
            await self._controller.set_hsv(brightness=100)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        await self._controller.set_hsv(brightness=0)

    def on_transition_finished(self, name: str, requeued: bool) -> None:
        event_data = {
            "device_id": "rgbwwid",
            "type": "transition_finished",
            "name": name,
            "requeued": requeued,
        }
        self._hass.bus.async_fire("transition_finished", event_data)

    def on_config_update(self, config: dict) -> None:
        self._attr_max_color_temp_kelvin = config["color"]["colortemp"]["cw"]
        self._attr_min_color_temp_kelvin = config["color"]["colortemp"]["ww"]
        self.async_write_ha_state()
