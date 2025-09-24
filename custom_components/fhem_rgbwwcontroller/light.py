from typing import Any
from .entity import RgbwwDevice
from .rgbww_controller import (
    RgbwwController,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    DEFAULT_MAX_KELVIN,
    DEFAULT_MIN_KELVIN,
    ColorMode,
    LightEntity,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Abode light devices."""

    async_add_entities(
        RgbwwLight(device) for device in data.abode.get_devices(generic_type="light")
    )


class RgbwwLight(RgbwwDevice, LightEntity):
    """Representation of an Abode light."""

    _device: RgbwwController | None = None
    _attr_name = None
    _attr_max_color_temp_kelvin = DEFAULT_MAX_KELVIN
    _attr_min_color_temp_kelvin = DEFAULT_MIN_KELVIN

    def turn_on(self, **kwargs: Any) -> None:
        """Turn on the light."""
        if ATTR_COLOR_TEMP_KELVIN in kwargs and self._device.is_color_capable:
            self._device.set_color_temp(kwargs[ATTR_COLOR_TEMP_KELVIN])
            return

        if ATTR_HS_COLOR in kwargs and self._device.is_color_capable:
            self._device.set_color(kwargs[ATTR_HS_COLOR])
            return

        if ATTR_BRIGHTNESS in kwargs and self._device.is_dimmable:
            # Convert Home Assistant brightness (0-255) to Abode brightness (0-99)
            # If 100 is sent to Abode, response is 99 causing an error
            self._device.set_level(ceil(kwargs[ATTR_BRIGHTNESS] * 99 / 255.0))
            return

        self._device.switch_on()

    def turn_off(self, **kwargs: Any) -> None:
        """Turn off the light."""
        self._device.switch_off()

    @property
    def is_on(self) -> bool:
        """Return true if device is on."""
        return bool(self._device.is_on)

    @property
    def brightness(self) -> int | None:
        """Return the brightness of the light."""
        if self._device.is_dimmable and self._device.has_brightness:
            brightness = int(self._device.brightness)
            # Abode returns 100 during device initialization and device refresh
            # Convert Abode brightness (0-99) to Home Assistant brightness (0-255)
            return 255 if brightness == 100 else ceil(brightness * 255 / 99.0)
        return None

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the color temp of the light."""
        return self._device.state.color_temp

    @property
    def hs_color(self) -> tuple[float, float] | None:
        """Return the color of the light."""
        match self._device.state.color_mode:
            case "hsv":
                return self._device.state.hue, self._device.state.saturation
            case "raw":
                return None
            case _ as e:
                raise RuntimeError(f"Unknown color mode: {e}")

    @property
    def color_mode(self) -> str | None:
        """Return the color mode of the light."""
        if self._device.is_dimmable and self._device.is_color_capable:
            if self.hs_color is not None:
                return ColorMode.HS
            return ColorMode.COLOR_TEMP
        if self._device.is_dimmable:
            return ColorMode.BRIGHTNESS
        return ColorMode.ONOFF

    @property
    def supported_color_modes(self) -> set[str] | None:
        """Flag supported color modes."""
        if self._device.is_dimmable and self._device.is_color_capable:
            return {ColorMode.COLOR_TEMP, ColorMode.HS}
        if self._device.is_dimmable:
            return {ColorMode.BRIGHTNESS}
        return {ColorMode.ONOFF}
