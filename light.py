"""Platform for light integration."""
import math
from datetime import timedelta

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

SCAN_INTERVAL = timedelta(seconds=2)

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
        self._state = [0, 0, 0, 0]

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
        # Any brightness means light is on.
        return self._state[2] > 0

    @property
    def hs_color(self):
        """Return the hue and saturation color value [float, float]."""
        h, s, _, _ = self._state
        h = h / 65535 * 360
        s = s / 65535 * 100
        return (h, s) if s else None

    @property
    def brightness(self):
        """Return the brightness of the light."""
        return self._state[2] / 65535 * 255

    @property
    def color_temp(self):
        """Return the CT color value in mireds."""
        _, s, _, k = self._state

        # If we got a saturation value, it means that light has
        # a color set and no temperature (temperature requires
        # light to be white, ie s == 0)
        if s:
            return None
        return color_util.color_temperature_kelvin_to_mired(k)

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
        h = 65535
        s = 65535
        b = 65535
        k = 3500

        # if the ligth was turned off, we want to power it and start
        # with all zones dimmed down.
        if self._mz_light.get_power() < 1:
            self._mz_light.set_zone_colors(list(map(lambda x: [x[0], x[1], 0, x[3]], self._mz_light.get_color_zones())))
            self._mz_light.set_power(True)

        self._mz_light.set_zone_color(self._zone_start, self._zone_end, [h, s, b, k], 500)

        # Avoid state ping-pong by holding off updates as the state settles
        #time.sleep(0.3)

    def turn_off(self, **kwargs):
        """Instruct the light to turn off."""
        new_state = self._state
        new_state[2] = 0
        self._mz_light.set_zone_color(self._zone_start, self._zone_end, new_state, 500)

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

        # Reduce the list to a single value. We mostly care
        # about the brightness here, to determine whether the
        # light is on. It's important the list is sorted.
        # For the others, we might get any value.
        h = sorted(list(hue_values))[-1]
        s = sorted(list(saturation_values))[-1]
        b = sorted(list(brightness_values))[-1]
        k = sorted(list(kelvin_values))[-1]

        self._state = [h, s, b, k]

