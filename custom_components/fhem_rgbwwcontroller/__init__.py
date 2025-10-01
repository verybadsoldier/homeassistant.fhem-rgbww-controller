"""The FHEM RGBWW Controller integration."""

from __future__ import annotations


from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ConfigEntryNotReady
from .const import DOMAIN
from .rgbww_controller import RgbwwController
from homeassistant.helpers import device_registry, entity_platform
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers import (
    config_validation as cv,
    service,
)
import logging
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_logger = logging.getLogger(__name__)

_PLATFORMS: list[Platform] = [Platform.LIGHT]

ATTR_NAME = "name"
DEFAULT_NAME = "World"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up FHEM RGBWW Controller from a config entry."""
    # Your TODOs for creating and storing an API instance are correct.
    # This is where you would connect to your device.
    # For now, we'll assume no connection is needed for testing.
    # entry.runtime_data = MyAPI(...)

    """Set up My RGB Controller from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Extrahiere Host aus dem ConfigEntry
    host = entry.data[CONF_HOST]

    # Erstelle eine Hub-Instanz für DIESES GERÄT
    # Wir übergeben die entry.unique_id (also die IP) für eine eindeutige Identifikation
    controller = RgbwwController(host)
    await controller.connect()

    entry.runtime_data = controller

    # --- DEVICE REGISTRATION ---
    # This is the new part. We create a device in the registry.
    dev_reg = device_registry.async_get(hass)
    dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.unique_id)},  # Must be a set of tuples
        name=entry.title,  # The name the user gave in the config flow
        manufacturer="Homebrew Hardware",
        model="FHEM RGBWW LED Controller",  # Replace with actual model
        #sw_version=f"{controller.info['git_version']} (WebApp:{controller.info['webapp_version']})",
    )
    # --- END DEVICE REGISTRATION ---

    # This forwards the setup to your light.py file.
    await hass.config_entries.async_forward_entry_setups(entry, _PLATFORMS)

    # Disconnect when the entry gets unloaded
    entry.async_on_unload(controller.disconnect)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # This function is required for reloading and removing the integration.
    return await hass.config_entries.async_unload_platforms(entry, _PLATFORMS)
