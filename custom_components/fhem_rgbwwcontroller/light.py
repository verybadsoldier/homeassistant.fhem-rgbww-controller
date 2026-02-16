"""Light platform for the fhem led controller integration."""

import functools
import logging
from typing import Any, cast

import voluptuous as vol

from .rgbww_entity import RgbwwEntity
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
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform

# Import the device class from the component that you want to support
import homeassistant.helpers.config_validation as cv
import homeassistant.helpers.device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.scaling import (
    scale_ranged_value_to_int_range,
    scale_to_ranged_value,
)

from .const import (
    ATTR_ANIM_DEFINITION_LIST,
    ATTR_CH_BLUE,
    ATTR_CH_CW,
    ATTR_CH_GREEN,
    ATTR_CH_RED,
    ATTR_CH_WW,
    ATTR_HUE,
    ATTR_QUEUE_POLICY,
    ATTR_REQUEUE,
    ATTR_ANIM_NAME,
    ATTR_SATURATION,
    ATTR_STAY,
    ATTR_TRANSITION_MODE,
    ATTR_TRANSITION_VALUE,
    DOMAIN,
)
from .core.color_commands import (
    ChannelsType,
    ColorCommandHsv,
    ColorCommandRgbww,
    parse_color_commands,
)
from .core.rgbww_controller import ControllerUnavailableError, RgbwwController

SERVICE_ANIMATION_HSV = "animation_hsv"
SERVICE_ANIMATION_CLI_HSV = "animation_cli_hsv"
SERVICE_ANIMATION_RGBWW = "animation_rgbww"
SERVICE_ANIMATION_CLI_RGBWW = "animation_cli_rgbww"
SERVICE_PAUSE = "PAUSE"
SERVICE_CONTINUE = "CONTINUE"
SERVICE_SKIP = "SKIP"
SERVICE_STOP = "STOP"

_SERVICE_ATTR_ANIM_CLI_COMMAND = "anim_definition_command"


_logger = logging.getLogger(__name__)


def _get_animation_service_base_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(ATTR_TRANSITION_MODE, default=None): vol.Maybe(
                vol.In(["time", "speed"])
            ),
            vol.Optional(ATTR_TRANSITION_VALUE, default=None): vol.Maybe(
                vol.All(vol.Coerce(int), vol.Range(min=0))
            ),
            vol.Optional(ATTR_STAY, default=None): vol.Maybe(
                vol.All(vol.Coerce(int), vol.Range(min=0))
            ),
            vol.Optional(ATTR_QUEUE_POLICY, default=None): vol.Maybe(
                vol.In(["single", "back", "front", "front_reset"])
            ),
            vol.Optional(ATTR_REQUEUE, default=None): vol.Maybe(cv.boolean),
            vol.Optional(ATTR_ANIM_NAME, default=None): vol.Maybe(cv.string),
        }
    )


def _register_channel_services():
    async def on_service_channel(light_entity: RgbwwLight, call: ServiceCall) -> None:
        """Handle the channel service call."""
        _logger.debug(
            "Channel service called for entity %s. Channel: %s",
            light_entity.entity_id,
            call.service,
        )

        await light_entity.service_channel(call)

    COMMAND_OPTIONS = ["pause", "stop", "continue"]
    CHANNEL_OPTIONS = ["hue", "saturation", "value", "color_temp"]

    # Definition des Service-Schemas
    CONTROL_CHANNEL_SCHEMA = {
        vol.Required("entity_id"): cv.entity_ids,
        # Validierung für das 'command'-Feld
        vol.Required("command"): vol.In(COMMAND_OPTIONS),
        # Validierung für das 'channels'-Feld
        # 'cv.ensure_list' stellt sicher, dass der Input eine Liste ist (auch wenn nur ein Element kommt).
        # Der innere Teil validiert, dass jeder String in der Liste ein gültiger Kanal ist.
        vol.Required("channels"): cv.ensure_list(vol.In(CHANNEL_OPTIONS)),
    }

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "control_channel",
        CONTROL_CHANNEL_SCHEMA,
        on_service_channel,
    )


def _register_animation_hsv_service():
    # This schema defines the structure for a single step in the animation sequence.
    # It corresponds to one object in the 'anim_definition' list.
    ANIMATION_STEP_SCHEMA = _get_animation_service_base_schema().extend(
        {
            vol.Optional(ATTR_HUE, default=None): vol.Maybe(cv.string),
            vol.Optional(ATTR_SATURATION, default=None): vol.Maybe(cv.string),
            vol.Optional(ATTR_BRIGHTNESS, default=None): vol.Maybe(cv.string),
            vol.Optional(ATTR_COLOR_TEMP_KELVIN, default=None): vol.Maybe(cv.string),
        }
    )

    # This is the main schema for the 'animation' service call.
    ANIMATION_SERVICE_SCHEMA = {
        # Validate that an entity_id is provided, which is standard for services
        # targeting an entity.
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        # Validate the main field 'anim_definition'.
        vol.Required(ATTR_ANIM_DEFINITION_LIST): vol.All(
            # 1. Ensure the input is a list.
            cv.ensure_list,
            # 2. Apply the ANIMATION_STEP_SCHEMA to each item in the list.
            [ANIMATION_STEP_SCHEMA],
            # 3. Ensure the list is not empty, as per your description.
            vol.Length(min=1),
        ),
    }

    async def on_service_animation_hsv(
        light_entity: RgbwwLight, call: ServiceCall
    ) -> None:
        """Handle the animation service call."""
        _logger.debug("Animation service called for entity %s", light_entity.entity_id)

        await light_entity.service_animation_hsv(call)

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_ANIMATION_HSV,
        ANIMATION_SERVICE_SCHEMA,
        on_service_animation_hsv,
    )

    async def on_service_animation_cli_hsv(
        light_entity: RgbwwLight, call: ServiceCall
    ) -> None:
        _logger.debug(
            "Animation HSV CLI service called for entity %s", light_entity.entity_id
        )

        await light_entity.service_animation_cli_hsv(call)

    ANIMATION_CLI_SERVICE_SCHEMA = {
        vol.Required(_SERVICE_ATTR_ANIM_CLI_COMMAND): cv.string
    }

    platform.async_register_entity_service(
        SERVICE_ANIMATION_CLI_HSV,
        ANIMATION_CLI_SERVICE_SCHEMA,
        on_service_animation_cli_hsv,
    )


def _register_animation_rgbww_service():
    # This schema defines the structure for a single step in the animation sequence.
    # It corresponds to one object in the 'anim_definition' list.
    ANIMATION_STEP_SCHEMA = _get_animation_service_base_schema().extend(
        {
            vol.Optional(ATTR_CH_RED, default=None): vol.Maybe(cv.string),
            vol.Optional(ATTR_CH_GREEN, default=None): vol.Maybe(cv.string),
            vol.Optional(ATTR_CH_BLUE, default=None): vol.Maybe(cv.string),
            vol.Optional(ATTR_CH_CW, default=None): vol.Maybe(cv.string),
            vol.Optional(ATTR_CH_WW, default=None): vol.Maybe(cv.string),
        }
    )

    # This is the main schema for the 'animation' service call.
    ANIMATION_SERVICE_SCHEMA = {
        # Validate that an entity_id is provided, which is standard for services
        # targeting an entity.
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        # Validate the main field 'anim_definition'.
        vol.Required(ATTR_ANIM_DEFINITION_LIST): vol.All(
            cv.ensure_list,
            [ANIMATION_STEP_SCHEMA],
            vol.Length(min=1),
        ),
    }

    async def on_service_animation_rgbww(
        light_entity: RgbwwLight, call: ServiceCall
    ) -> None:
        """Handle the animation service call."""
        _logger.debug("Animation service called for entity %s", light_entity.entity_id)

        await light_entity.service_animation_rgbww(call)

    # Register the service to set HSV with advanced options
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_ANIMATION_RGBWW,
        ANIMATION_SERVICE_SCHEMA,
        on_service_animation_rgbww,
    )

    async def on_service_animation_cli_rgbww(
        light_entity: RgbwwLight, call: ServiceCall
    ) -> None:
        _logger.debug(
            "Animation HSV CLI service called for entity %s", light_entity.entity_id
        )

        await light_entity.service_animation_cli_rgbww(call)

    ANIMATION_CLI_SERVICE_SCHEMA = {
        vol.Required(_SERVICE_ATTR_ANIM_CLI_COMMAND): cv.string
    }

    platform.async_register_entity_service(
        SERVICE_ANIMATION_CLI_RGBWW,
        ANIMATION_CLI_SERVICE_SCHEMA,
        on_service_animation_cli_rgbww,
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    controller = cast(RgbwwController, entry.runtime_data)

    rgb = RgbwwLight(
        hass,
        controller,
        entry,
    )

    async_add_entities((rgb,))

    _register_animation_hsv_service()
    _register_animation_rgbww_service()
    _register_channel_services()


class RgbwwLight(RgbwwEntity, LightEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False

    _attr_max_color_temp_kelvin = DEFAULT_MAX_KELVIN
    _attr_min_color_temp_kelvin = DEFAULT_MIN_KELVIN

    def __init__(
        self,
        hass: HomeAssistant,
        controller: RgbwwController,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the light."""
        super().__init__(
            hass=hass, controller=controller, device_id=config_entry.unique_id
        )

        # if unique_id is not None:
        #    self._attr_unique_id = unique_id + "_light"
        self._attr_name = config_entry.title + " Light"
        self._attr_unique_id = f"{config_entry.unique_id}_lightunique"

        self._attr_supported_color_modes = {
            # ColorMode.ONOFF,
            ColorMode.HS,
            ColorMode.COLOR_TEMP,
        }
        self._attr_supported_features = (
            LightEntityFeature.TRANSITION | LightEntityFeature.FLASH
            # | LightEntityFeature.EFFECT
        )
        # Initialize the attributes dictionary
        self._attr_extra_state_attributes = {}

        # self._attr_effect_list = ["Pause", "Continue", "Skip", "Stop"]
        # self._attr_effect = None

    async def async_added_to_hass(self) -> None:
        """Subscribe to the events."""
        await super().async_added_to_hass()

        if self._controller.state_completed:
            self.on_state_completed()

    async def async_will_remove_from_hass(self) -> None:
        await super().async_will_remove_from_hass()

    def on_clock_slave_status_update(self) -> None: ...  # noqa: D102

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
                self._attr_extra_state_attributes["hsv_ct"] = (
                    self._controller.color.color_temp
                )
                self._attr_is_on = v > 0
                # self._attr_color_temp_kelvin = self._controller.color.color_temp
                # self._attr_color_mode = ColorMode.HS
            case _:
                ...
        self._attr_color_mode = ColorMode.HS
        self.async_write_ha_state()

    def _update_ha_device(self) -> None:
        device_registry = dr.async_get(self.hass)

        device_entry = device_registry.async_get_device(
            identifiers={(DOMAIN, self._device_id)}
        )

        assert device_entry is not None

        updated_info = {
            "sw_version": f"{self._controller.info['git_version']} (WebApp:{self._controller.info['webapp_version']})",
            # can not be altered later: "connections": {("mac", self._controller.info["connection"]["mac"])},
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
                    scale_ranged_value_to_int_range, (0, 255), (0, 1023)
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
                hsv_params = {"h": hs[0], "s": hs[1], "speed_or_fade_duration": 500}
                self._attr_hs_color = hs
                # self._attr_color_mode = ColorMode.RGBWW
            if (ct := kwargs.get(ATTR_COLOR_TEMP_KELVIN)) is not None:
                hsv_params["ct"] = ct
                self._attr_color_temp_kelvin = ct
                # we do not actually switch to color temp mode because we use it as a feature for hsv
                # self._attr_color_mode = ColorMode.COLOR_TEMP

            if not kwargs:  # Turn on with last known state or default
                await self._controller.send_color_command(ColorCommandHsv(v=100))

            if (brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None:
                hsv_params["v"] = scale_to_ranged_value((0, 255), (0, 100), brightness)
                self._attr_brightness = brightness
                self._attr_is_on = brightness > 0
            if (transition := kwargs.get(ATTR_TRANSITION)) is not None:
                hsv_params["speed_or_fade_duration"] = int(
                    transition * 1000
                )  # seconds to milliseconds

        except ControllerUnavailableError as e:
            _logger.error("async_turn_on failed. Controller error: %s", e)
        else:
            if hsv_params:
                await self._controller.send_color_command(ColorCommandHsv(**hsv_params))
                attr_changed = True

            if attr_changed:
                self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        await self._controller.send_color_command(ColorCommandHsv(v=0))

    def on_transition_finished(self, name: str, requeued: bool) -> None:
        event_data: dict[str, Any] = {
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

    async def service_animation_cli_hsv(self, call: ServiceCall) -> None:
        try:
            anims = parse_color_commands(
                call.data[_SERVICE_ATTR_ANIM_CLI_COMMAND], ChannelsType.HSV
            )
            await self._controller.send_color_commands(anims)
        except ControllerUnavailableError as e:
            # Catch specific errors from your controller library
            _logger.error(
                "Animation failed: Device at %s is unavailable. Error: %s",
                self._controller.host,  # Assuming controller has an IP property
                e,
            )
            # Optionally, re-raise as a HA error to notify the user in the UI
            raise HomeAssistantError(
                f"Failed to start animation: {self.name} is unavailable."
            ) from e

    async def service_animation_hsv(self, call: ServiceCall) -> None:
        try:
            color_commands = [
                ColorCommandHsv.from_service(cmd)
                for cmd in call.data[ATTR_ANIM_DEFINITION_LIST]
            ]
            await self._controller.send_color_commands(color_commands)
        except ControllerUnavailableError as e:
            # Catch specific errors from your controller library
            _logger.error(
                "Animation failed: Device at %s is unavailable. Error: %s",
                self._controller.host,  # Assuming controller has an IP property
                e,
            )
            # Optionally, re-raise as a HA error to notify the user in the UI
            raise HomeAssistantError(
                f"Failed to start animation: {self.name} is unavailable."
            ) from e
        except Exception as e:
            _logger.error(
                "Animation failed: Error: %s",
                self._controller.host,
                e,
            )
            raise HomeAssistantError(f"Failed to start animation. Error: {e}") from e

    async def service_animation_cli_rgbww(self, call: ServiceCall) -> None:
        try:
            anims = parse_color_commands(
                call.data[_SERVICE_ATTR_ANIM_CLI_COMMAND], ChannelsType.RGBWW
            )
            await self._controller.send_color_commands(anims)
        except ControllerUnavailableError as e:
            # Catch specific errors from your controller library
            _logger.error(
                "Animation failed: Device at %s is unavailable. Error: %s",
                self._controller.host,  # Assuming controller has an IP property
                e,
            )
            # Optionally, re-raise as a HA error to notify the user in the UI
            raise HomeAssistantError(
                f"Failed to start animation: {self.name} is unavailable."
            ) from e

    async def service_animation_rgbww(self, call: ServiceCall) -> None:
        try:
            color_commands = [
                ColorCommandRgbww.from_service(cmd)
                for cmd in call.data[ATTR_ANIM_DEFINITION_LIST]
            ]
            await self._controller.send_color_commands(color_commands)
        except ControllerUnavailableError as e:
            # Catch specific errors from your controller library
            _logger.error(
                "Animation failed: Device at %s is unavailable. Error: %s",
                self._controller.host,  # Assuming controller has an IP property
                e,
            )
            # Optionally, re-raise as a HA error to notify the user in the UI
            raise HomeAssistantError(
                f"Failed to start animation: {self.name} is unavailable."
            ) from e
        except Exception as e:
            _logger.error(
                "Animation failed: Error: %s",
                self._controller.host,
                e,
            )
            raise HomeAssistantError(f"Failed to start animation. Error: {e}") from e

    async def service_channel(self, call: ServiceCall) -> None:
        try:
            await self._controller.send_channel_command(
                call.data["command"],
                call.data["channels"],
            )

        except ControllerUnavailableError as e:
            # Catch specific errors from your controller library
            _logger.error(
                "Channel command failed: Device at %s is unavailable. Error: %s",
                self._controller.host,  # Assuming controller has an IP property
                e,
            )
            # Optionally, re-raise as a HA error to notify the user in the UI
            raise HomeAssistantError(
                f"Failed to send channel command: {self.name} is unavailable."
            ) from e
        except Exception as e:
            _logger.error(
                "Animation failed: Error: %s",
                self._controller.host,
                e,
            )
            raise HomeAssistantError(f"Failed to start animation. Error: {e}") from e
