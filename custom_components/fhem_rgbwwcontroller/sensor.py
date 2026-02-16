"""Platform for sensor integration."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import cast

import voluptuous as vol

from .core.rgbww_controller import (
    RgbwwController,
)
from .rgbww_entity import RgbwwEntity
from homeassistant.components.sensor import (
    PLATFORM_SCHEMA as SENSOR_PLATFORM_SCHEMA,
    SensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

_LOGGER = logging.getLogger(__name__)

CONF_METER_NUMBER = "meter_number"

SCAN_INTERVAL = timedelta(minutes=15)

PLATFORM_SCHEMA = SENSOR_PLATFORM_SCHEMA.extend(
    {vol.Required(CONF_METER_NUMBER): cv.string}
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    controller = cast(RgbwwController, entry.runtime_data)

    sync_offset = SyncOffsetSensor(hass, controller, entry)

    async_add_entities((sync_offset,))


class SyncOffsetSensor(RgbwwEntity, SensorEntity):
    # _attr_device_class = SensorDeviceClass.

    def __init__(
        self,
        hass: HomeAssistant,
        controller: RgbwwController,
        config_entry: ConfigEntry,
    ):
        """Initialize the sensor."""
        super().__init__(
            hass=hass, controller=controller, device_id=config_entry.unique_id
        )

        self._attr_name = config_entry.title + " SyncOffet"
        self._attr_unique_id = f"{config_entry.unique_id}_syncoffset"
        self._attr_native_unit_of_measurement = "sync cycles"

    async def async_added_to_hass(self) -> None:
        """Subscribe to the events."""
        self._controller.register_callback(self)

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from the events."""
        await super().async_will_remove_from_hass()

    def on_update_color(self) -> None: ...
    def on_connection_update(self) -> None: ...
    def on_transition_finished(self, name: str, requeued: bool) -> None: ...

    def on_config_update(self) -> None:
        self._attr_available = self._controller.config["sync"]["cmd_slave_enabled"]
        self.async_write_ha_state()

    def on_state_completed(self) -> None:
        self._attr_available = True

    def on_clock_slave_status_update(self) -> None:
        self._attr_native_value = self._controller.clock_slave_status["offset"]
        # clockCurrentInterval
        self.async_write_ha_state()
