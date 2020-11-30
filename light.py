"""Platform for light integration."""
import logging

import voluptuous as vol

import homeassistant.helpers.config_validation as cv
# Import the device class from the component that you want to support
from homeassistant.components.light import (
    ATTR_BRIGHTNESS, PLATFORM_SCHEMA, LightEntity)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from lifxlan import LifxLAN
from lifxlan import MultiZoneLight

_LOGGER = logging.getLogger(__name__)

# Validation of the user's configuration
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required('name'): cv.string,
    vol.Required('zone_start'): cv.string,
    vol.Required('zone_end'): cv.string,
})


def setup_platform(hass, config, add_entities, discovery_info=None):
    # Assign configuration variables.
    # The configuration check takes care they are present.
    #host = config[CONF_HOST]

    # Verify that passed in configuration works
    if False:
        _LOGGER.error("Could not connect to AwesomeLight hub")
        return

    # Add devices
    target_light = MultiZoneLight("d0:73:d5:42:29:97", "10.18.0.194")

    add_entities([LIFXVirtualLight(target_light, 0, 31)])


class LIFXVirtualLight(LightEntity):

    def __init__(self, mz_light, zone_start, zone_end):
        """Initialize a Virtual Light."""
        self._mz_light = mz_light
        self._zone_start = zone_start
        self._zone_end = zone_end
        self._name = "mansarda 1"
        self._state = None
        self._brightness = None

    @property
    def name(self):
        """Return the display name of this light."""
        return self._name

    @property
    def brightness(self):
        """Return the brightness of the light."""
        return self._brightness

    @property
    def is_on(self):
        """Return true if light is on."""
        return self._state

    def turn_on(self, **kwargs):
        """Instruct the light to turn on."""
        #self._light.brightness = kwargs.get(ATTR_BRIGHTNESS, 255)
        #self._light.turn_on()

    def turn_off(self, **kwargs):
        """Instruct the light to turn off."""
        #self._light.turn_off()

    def update(self):
        """Fetch new state data for this light."""
        #self._light.update()
        self._state = True
        self._brightness = 42
