from .core.rgbww_controller import (
    RgbwwController,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, Entity

from .const import DOMAIN  # It's good practice to have a const.py for your domain


class RgbwwEntity(Entity):
    """A base class for all My RGBWW Controller entities."""

    _attr_has_entity_name = True

    def __init__(
        self, hass: HomeAssistant, controller: RgbwwController, device_id: str, **kwargs
    ) -> None:
        """Initialize the base entity."""
        super().__init__(**kwargs)
        self._device_id = device_id
        self._controller = controller
        self.hass = hass

        # ✅ Define DeviceInfo once in this central location
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name="FHEM RGBWW LED Controller",
            manufacturer="FHEM Community :)",
            # model="",
            # sw_version="1.0",
            # connections={("mac", mac_address)} if mac_address else None,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to the events."""
        self._controller.register_callback(self)

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from the events."""
        self._controller.unregister_callback(self)

        await super().async_will_remove_from_hass()

    def on_update_color(self) -> None: ...
    def on_connection_update(self) -> None: ...
    def on_transition_finished(self, name: str, requeued: bool) -> None: ...
    def on_config_update(self) -> None: ...
    def on_state_completed(self) -> None:
        self._attr_available = True

    def on_clock_slave_status_update(self) -> None:
        self._attr_native_value = self._controller.clock_slave_status["offset"]
        # clockCurrentInterval
        self.async_write_ha_state()
