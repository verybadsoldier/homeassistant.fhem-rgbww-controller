from homeassistant.const import (
    CONF_ENTITY_ID,
    CONF_TYPE,
    CONF_NAME,
    CONF_DEVICE_ID,
    CONF_DOMAIN,
)
from homeassistant.helpers.config_validation import TRIGGER_BASE_SCHEMA
import voluptuous as vol
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.typing import ConfigType
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA


TRIGGER_TYPES = {"transition_finished"}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        #    vol.Required("type"): "transition_finished",
    }
)

from .const import DOMAIN

from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_PLATFORM,
    CONF_TYPE,
)

from homeassistant.helpers import device_registry as dr


async def async_get_triggers(hass, device_id):
    """Return a list of supported triggers."""

    triggers = []
    triggers.append(
        TRIGGER_SCHEMA(
            {
                # Required fields of TRIGGER_BASE_SCHEMA
                CONF_PLATFORM: "device",
                CONF_DOMAIN: DOMAIN,
                CONF_DEVICE_ID: device_id,
                # Required fields of TRIGGER_SCHEMA
                CONF_TYPE: "transition_finished",
            }
        )
    )

    return triggers


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: CALLBACK_TYPE,
    trigger_info: dict,
):
    """Called when a user creates a trigger in the UI."""
    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: "event",
            event_trigger.CONF_EVENT_TYPE: "transition_finished",
            event_trigger.CONF_EVENT_DATA: {
                CONF_DEVICE_ID: config[CONF_DEVICE_ID],
                CONF_TYPE: config[CONF_TYPE],
            },
        }
    )
    return await event_trigger.async_attach_trigger(
        hass, event_config, action, trigger_info
    )
