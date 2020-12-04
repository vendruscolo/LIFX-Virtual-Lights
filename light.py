"""Platform for light integration."""
import math

import logging

import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    PLATFORM_SCHEMA,
    SUPPORT_BRIGHTNESS,
    SUPPORT_COLOR,
    SUPPORT_COLOR_TEMP,
    SUPPORT_TRANSITION,
    LightEntity)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
import homeassistant.util.color as color_util

from lifxlan import LifxLAN
from lifxlan import MultiZoneLight

from .const import (
    DOMAIN,
    CONF_TARGET_LIGHT,
    CONF_ZONE_START,
    CONF_ZONE_END
)

_LOGGER = logging.getLogger(__name__)

# Validation of the user's configuration
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required('name'): cv.string,
    vol.Required(CONF_ZONE_START): cv.string,
    vol.Required(CONF_ZONE_END): cv.string,
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

        self._is_on = False
        self._brightness = None
        self._color_temp = None
        self._hs_color = None

    @property
    def name(self):
        """Return the display name of this light."""
        return self._name

    @property
    def unique_id(self):
        """Return the unique id of this light."""
        return "mansarda_1"

    @property
    def supported_featured(self):
        return SUPPORT_BRIGHTNESS | SUPPORT_COLOR | SUPPORT_COLOR_TEMP | SUPPORT_TRANSITION

    @property
    def is_on(self):
        """Return true if light is on."""
        return self._state

    @property
    def brightness(self):
        """Return the brightness of the light."""
        return self._brightness

    @property
    def color_temp(self):
        """Return the CT color value in mireds."""
        return self._color_temp

    @property
    def max_mireds(self):
        """Return the warmest color_temp that this light supports."""
        return math.ceil(color_util.color_temperature_kelvin_to_mired(9000))

    @property
    def min_mireds(self):
        """Return the coldest color_temp that this light supports."""
        return math.ceil(color_util.color_temperature_kelvin_to_mired(2500))

    @property
    def hs_color(self):
        """Return the hue and saturation color value [float, float]."""
        return self._hs_color

    def turn_on(self, **kwargs):
        """Instruct the light to turn on."""
        #self._light.brightness = kwargs.get(ATTR_BRIGHTNESS, 255)
        #self._light.turn_on()

    def turn_off(self, **kwargs):
        """Instruct the light to turn off."""
        #self._light.turn_off()

    def update(self):
        """Fetch new state data for this light."""
        zones = self._mz_light.get_color_zones()

        hue_values = set()
        saturation_values = set()
        brightness_values = set()
        kelvin_values = set()
        for zone in zones[self._zone_start:self._zone_end]:
            hue_values.add(zone[0])
            saturation_values.add(zone[1])
            brightness_values.add(zone[2])
            kelvin_values.add(zone[3])

        h = sorted(list(hue_values))[-1] * 360 / 65535
        s = sorted(list(saturation_values))[-1] * 100 / 65535
        b = sorted(list(brightness_values))[-1] * 255 / 65535
        k = math.ceil(color_util.color_temperature_kelvin_to_mired(sorted(list(kelvin_values))[-1]))

        self._hs_color = [h, s]
        self._brightness = b
        self._color_temp = k

        self._is_on = sorted(list(brightness_values))[-1] > 0

