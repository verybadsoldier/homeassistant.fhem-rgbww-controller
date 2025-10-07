"""Light platform for the fhem led controller integration."""

import logging
from typing import Any, cast

import voluptuous as vol
import functools
from homeassistant.util.scaling import (
    scale_ranged_value_to_int_range,
    scale_to_ranged_value,
)

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ATTR_RGBWW_COLOR,
    ATTR_TRANSITION,
    DEFAULT_MAX_KELVIN,
    DEFAULT_MIN_KELVIN,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import entity_platform

# Import the device class from the component that you want to support
import homeassistant.helpers.config_validation as cv
import homeassistant.helpers.device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .rgbww_controller import ControllerUnavailableError, RgbwwController

from .animation_syntax import parse_animation_commands

SERVICE_ANIMATION = "animation"
SERVICE_PAUSE = "PAUSE"
SERVICE_CONTINUE = "CONTINUE"
SERVICE_SKIP = "SKIP"
SERVICE_STOP = "STOP"

_logger = logging.getLogger(__name__)


def _service_animation(self, call: ServiceCall) -> None:
    anims = parse_animation_commands(call.data["anim_definition"])
    ...



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
        SERVICE_ANIMATION,
        {vol.Required("anim_definition"): cv.string},
        _service_animation,
    )

    #platform.async_register_entity_service(
    #    SERVICE_PAUSE,
    #    vol.Schema(
    #        {
    #            vol.Required("all_channels"): bool,
    #            vol.Optional("channel_h"): bool,
    #            vol.Optional("channel_s"): bool,
    #            vol.Optional("channel_v"): bool,
    #        }
    #    ),
    #    _service_pause,
    #)


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

        # self.hass = hass
        # if unique_id is not None:
        #    self._attr_unique_id = unique_id + "_light"
        self._attr_name = entry.title + " Light"
        self._attr_unique_id = f"{entry.unique_id}_lightunique"

        self._attr_supported_color_modes = {
            # ColorMode.ONOFF,
            ColorMode.HS,
            ColorMode.COLOR_TEMP,
        }
        self._attr_supported_features = (
            LightEntityFeature.TRANSITION | LightEntityFeature.FLASH
            # | LightEntityFeature.EFFECT
        )

        # self._attr_effect_list = ["Pause", "Continue", "Skip", "Stop"]
        # self._attr_effect = None

    async def async_added_to_hass(self) -> None:
        """Subscribe to the events."""
        self._controller.register_callback(self)

        if self._controller.state_completed:
            self.on_state_completed()

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from the events."""
        self._controller.unregister_callback(self)

        await super().async_will_remove_from_hass()

    def on_update_color(self) -> None:  # noqa: D102
        if not self._controller.state_completed:
            return

        match self._controller.color.color_mode:
            case "raw":
                raw_conv = functools.partial(
                    scale_ranged_value_to_int_range, (0, 1023), (0, 255)
                )

                self._attr_rgbww_color = (
                    raw_conv(self._controller.color.raw_r),
                    raw_conv(self._controller.color.raw_g),
                    raw_conv(self._controller.color.raw_b),
                    raw_conv(self._controller.color.raw_cw),
                    raw_conv(self._controller.color.raw_ww),
                )
                self._attr_is_on = (
                    self._controller.color.raw_r > 0
                    or self._controller.color.raw_g > 0
                    or self._controller.color.raw_b > 0
                    or self._controller.color.raw_ww > 0
                    or self._controller.color.raw_cw > 0
                )
                # self._attr_color_mode = ColorMode.RGBWW
            case "hsv":
                self._attr_hs_color = (
                    self._controller.color.hue,
                    self._controller.color.saturation,
                )

                v = self._controller.color.brightness
                if v is not None:
                    self._attr_brightness = scale_ranged_value_to_int_range(
                        (0, 100), (0, 255), v
                    )

                self._attr_is_on = v > 0
                # self._attr_color_temp_kelvin = self._controller.color.color_temp
                # self._attr_color_mode = ColorMode.HS
            case _:
                ...
        self._attr_color_mode = ColorMode.HS
        self.async_write_ha_state()

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        di = DeviceInfo(
            identifiers={
                # Serial numbers are unique identifiers within a specific domain
                (DOMAIN, self.unique_id)
            },
            name=self.name,
            manufacturer="FHEM Community :)",
            model="FHEM RGBWW LED Controller",
            # sw_version=f"{self._controller.info['git_version']} (WebApp:{self._controller.info['webapp_version']})",
        )

        if self._controller.connected:
            di["sw_version"] = (
                f"{self._controller.info['git_version']} (WebApp:{self._controller.info['webapp_version']})"
            )
        return di

    def _update_ha_device(self) -> None:
        device_registry = dr.async_get(self.hass)

        device_entry = device_registry.async_get_device(
            identifiers={(DOMAIN, self.unique_id)}
        )

        assert device_entry is not None

        updated_info = {
            "sw_version": f"{self._controller.info['git_version']} (WebApp:{self._controller.info['webapp_version']})",
        }

        device_registry.async_update_device(
            device_id=device_entry.id,
            **updated_info,
        )

        self.async_write_ha_state()

    # protocol rgbww state
    def on_state_completed(self) -> None:
        self.on_update_color()  # Update color first to set color mode, otherwise brightness might be ignored
        self.on_connection_update()
        self.on_config_update()
        self._update_ha_device()
        self._attr_available = True

    def on_connection_update(self) -> None:
        if self._controller.connected:
            return
        self._attr_available = self._controller.connected
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        try:
            attr_changed = False
            hsv_params: dict[str, Any] = {}

            if (rgbww := kwargs.get(ATTR_RGBWW_COLOR)) is not None:
                raw_conv = functools.partial(
                    scale_ranged_value_to_int_range, (0, 255), (0, 1024)
                )

                ctrl_raw = (
                    raw_conv(rgbww[0]),
                    raw_conv(rgbww[1]),
                    raw_conv(rgbww[2]),
                    raw_conv(rgbww[3]),
                    raw_conv(rgbww[4]),
                )
                await self._controller.set_raw(*ctrl_raw)
                self._attr_rgbww_color = rgbww
                attr_changed = True
                self._attr_color_mode = ColorMode.RGBWW
                self._attr_is_on = any(c > 0 for c in rgbww)
            elif (hs := kwargs.get(ATTR_HS_COLOR)) is not None:
                hsv_params = {"hue": hs[0], "saturation": hs[1], "t": 500}
                self._attr_hs_color = hs
                # self._attr_color_mode = ColorMode.RGBWW
            if (ct := kwargs.get(ATTR_COLOR_TEMP_KELVIN)) is not None:
                hsv_params["ct"] = ct
                self._attr_color_temp_kelvin = ct
                # we do not actually switch to color temp mode because we use it as a feature for hsv
                # self._attr_color_mode = ColorMode.COLOR_TEMP

            if not kwargs:  # Turn on with last known state or default
                await self._controller.set_hsv(brightness=100)

            if (brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None:
                hsv_params["brightness"] = scale_to_ranged_value(
                    (0, 255), (0, 100), brightness
                )
                self._attr_brightness = brightness
                self._attr_is_on = brightness > 0
            if (transition := kwargs.get(ATTR_TRANSITION)) is not None:
                hsv_params["t"] = int(transition * 1000)  # seconds to milliseconds

        except ControllerUnavailableError as e:
            _logger.error("async_turn_on failed. Controller error: %s", e)
        else:
            if hsv_params:
                await self._controller.set_hsv(**hsv_params)
                attr_changed = True

            if attr_changed:
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

    def on_config_update(self) -> None:
        if not self._controller.state_completed:
            return

        self._attr_max_color_temp_kelvin = self._controller.config["color"][
            "colortemp"
        ]["cw"]
        self._attr_min_color_temp_kelvin = self._controller.config["color"][
            "colortemp"
        ]["ww"]
        self.async_write_ha_state()
