"""Platform for light integration."""
import math
import time
from datetime import timedelta

import logging

import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_HS_COLOR,
    ATTR_COLOR_TEMP,
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
    CONF_NAME,
    CONF_TARGET_LIGHT,
    CONF_ZONE_START,
    CONF_ZONE_END,
    CONF_TURN_ON_BRIGHTNESS
)

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=2)

# Validation of the user's configuration
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_NAME): cv.string,
    vol.Required(CONF_TARGET_LIGHT ): cv.string,
    vol.Required(CONF_ZONE_START): vol.Coerce(int),
    vol.Required(CONF_ZONE_END): vol.Coerce(int),
    vol.Optional(CONF_TURN_ON_BRIGHTNESS, default=255): vol.Coerce(int),
})


def setup_platform(hass, config, add_entities, discovery_info=None):
    # Assign configuration variables.
    # The configuration check takes care they are present.

    name = config[CONF_NAME]
    target = config[CONF_TARGET_LIGHT]
    zone_start = config[CONF_ZONE_START]
    zone_end = config[CONF_ZONE_END]
    turn_on_brightness = config[CONF_TURN_ON_BRIGHTNESS]

    # Verify that passed in configuration works
    if zone_end < zone_start:
        _LOGGER.error("Zone end must be greater than or equal to zone start")
        return

    # Search for devices matching the target
    target_light = MultiZoneLight(target, "10.18.0.194")
    add_entities([LIFXVirtualLight(target_light, name, zone_start, zone_end, turn_on_brightness)])


class LIFXVirtualLight(LightEntity):

    def __init__(self, mz_light, name, zone_start, zone_end, turn_on_brightness):
        """Initialize a Virtual Light."""
        self._mz_light = mz_light

        self._name = name
        self._zone_start = zone_start
        self._zone_end = zone_end

        self._turn_on_brightness = turn_on_brightness

        self._state = [0, 0, 0, 0]

    @property
    def name(self):
        """Return the display name of this light."""
        return self._name

    @property
    def unique_id(self):
        """Return the unique id of this light."""
        return self._name

    @property
    def supported_features(self):
        """Flag supported features."""
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
        return math.ceil(color_util.color_temperature_kelvin_to_mired(2500))

    @property
    def min_mireds(self):
        """Return the coldest color_temp that this light supports."""
        return math.ceil(color_util.color_temperature_kelvin_to_mired(9000))

    def turn_on(self, **kwargs):
        """Instruct the light to turn on."""

        # Grab the current state, and update that so it's consistent.
        h, s, b, k = self._state

        # We're turning on the light, which means that if it was 0, it was
        # previously turned off by the user (see turn_off).
        # So, if user is switching it on, bring it to full light. If it's
        # tweaking the setup (with the light already on), this value will be
        # eventually updated.
        # The flow is: turn off (b=0) -> turn on (b=255) -> change
        # color/brighthess. It's not possible to move from being off to a
        # different colored light.
        if b < 1:
            b = self._turn_on_brightness / 255 * 65535

        if ATTR_HS_COLOR in kwargs:
            hue, saturation = kwargs[ATTR_HS_COLOR]
            h = int(hue / 360 * 65535)
            s = int(saturation / 100 * 65535)
            k = 3500

        if ATTR_BRIGHTNESS in kwargs:
            b = kwargs[ATTR_BRIGHTNESS] / 255 * 65535

        if ATTR_COLOR_TEMP in kwargs:
            s = 0
            k = math.ceil(color_util.color_temperature_mired_to_kelvin(kwargs[ATTR_COLOR_TEMP]))

        # if the ligth was turned off, we want to power it and start
        # with all zones dimmed down.
        if self._mz_light.get_power() < 1:
            self._mz_light.set_zone_colors(list(map(lambda x: [x[0], x[1], 0, x[3]], self._mz_light.get_color_zones())))
            self._mz_light.set_power(True)

        self._mz_light.set_zone_color(self._zone_start, self._zone_end, [h, s, b, k])

        # Avoid state ping-pong by holding off updates as the state settles
        time.sleep(0.3)

    def turn_off(self, **kwargs):
        """Instruct the light to turn off."""
        new_state = self._state
        new_state[2] = 0
        self._mz_light.set_zone_color(self._zone_start, self._zone_end, new_state)

        # Avoid state ping-pong by holding off updates as the state settles
        time.sleep(0.3)

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

