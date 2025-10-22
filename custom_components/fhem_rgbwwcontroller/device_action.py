import voluptuous as vol

from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_ENTITY_ID, CONF_TYPE
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import config_validation as cv, entity_registry as er

from .const import DOMAIN  # Import your integration's domain

# Define constants for our action
ACTION_TYPE_TURN_ON_LOG = "turn_on_with_log"
CONF_MESSAGE = "message"

# Define the schema for the action in an automation
# ACTION_SCHEMA = cv.DEVICE_ACTION_BASE.extend(
#    {
#        vol.Required(CONF_TYPE): ACTION_TYPE_TURN_ON_LOG,
#        vol.Required(CONF_ENTITY_ID): cv.entity_id,
#        vol.Optional(CONF_MESSAGE): cv.string,
#    }
# )


async def async_get_actions(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, str]]:
    """Return the list of available actions for this device."""
    actions = []

    # Get the entity registry
    entity_registry = er.async_get(hass)

    # Find all entities for this device that are in our domain (or 'light')
    for entry in er.async_entries_for_device(entity_registry, device_id):
        if entry.domain == DOMAIN or entry.domain == "light":
            # Add our custom action
            actions.append(
                {
                    CONF_DOMAIN: DOMAIN,
                    CONF_DEVICE_ID: device_id,
                    CONF_ENTITY_ID: entry.entity_id,
                    CONF_TYPE: ACTION_TYPE_TURN_ON_LOG,
                }
            )

    return actions


async def async_get_action_capabilities(
    hass: HomeAssistant, config: dict
) -> dict[str, vol.Schema]:
    """Get the capabilities of a specific action."""
    # This is where we define the extra input fields for the UI
    if config[CONF_TYPE] == ACTION_TYPE_TURN_ON_LOG:
        return {
            "extra_fields": vol.Schema(
                {
                    # This creates a text box in the UI with the label "Message"
                    vol.Optional(CONF_MESSAGE): cv.string,
                }
            )
        }
    return {}


async def async_call_action(
    hass: HomeAssistant, config: dict, context: Context | None = None
) -> None:
    """Execute the action."""
    # Home Assistant will call this function when the automation runs.
    # It translates our action configuration into a service call.
    await hass.services.async_call(
        DOMAIN,
        ACTION_TYPE_TURN_ON_LOG,  # This is the name of our service
        {
            "entity_id": config[CONF_ENTITY_ID],
            "message": config.get(CONF_MESSAGE),  # Pass the message from the UI
        },
        blocking=True,
        context=context,
    )
