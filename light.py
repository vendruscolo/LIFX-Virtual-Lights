"""Platform for light integration."""
import math
import time
import asyncio
from datetime import timedelta
from collections import namedtuple

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
    LightEntity)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
import homeassistant.util.color as color_util

from photons_app.executor import library_setup
from photons_messages import DeviceMessages
from photons_messages import LightMessages
from photons_messages import MultiZoneMessages
from photons_app.special import HardCodedSerials

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

HSBK = namedtuple('HSBK', ['h', 's', 'b', 'k'])

collector = library_setup()
lan_target = collector.resolve_target("lan")

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    # Assign configuration variables.
    # The configuration check takes care they are present.

    name = config[CONF_NAME]
    mac_address = config[CONF_TARGET_LIGHT]
    zone_start = config[CONF_ZONE_START]
    zone_end = config[CONF_ZONE_END]
    turn_on_brightness = config[CONF_TURN_ON_BRIGHTNESS]

    # Verify that passed in configuration works
    if zone_end < zone_start:
        _LOGGER.error("Zone end must be greater than or equal to zone start")
        return

    reference = collector.reference_object(mac_address)
    sender = await lan_target.make_sender()

    async_add_entities([LIFXVirtualLight(sender, reference, mac_address, name, zone_start, zone_end, turn_on_brightness)])


class LIFXVirtualLight(LightEntity):

    def __init__(self, sender, reference, mac_address, name, zone_start, zone_end, turn_on_brightness):
        """Initialize a Virtual Light."""

        # Deps
        self._sender = sender
        self._reference = reference

        # Conf
        self._mac_address = mac_address
        self._name = name
        self._zone_start = zone_start
        self._zone_end = zone_end
        self._turn_on_brightness = turn_on_brightness

        # Cached values
        self._available = False
        self._current_color_zones = []
        self._hsbk = HSBK(0, 0, 0, 0)
        self._running_effect = False

    @property
    def name(self):
        """Return the display name of this light."""
        return self._name

    @property
    def unique_id(self):
        """Return the unique id of this light."""
        return self._mac_address + "|" + str(self._zone_start) + "|" + str(self._zone_end)

    @property
    def available(self):
        """Indicate if Home Assistant is able to read the state and control the underlying device."""
        return self._available

    @property
    def supported_features(self):
        """Flag supported features."""
        return SUPPORT_BRIGHTNESS | SUPPORT_COLOR | SUPPORT_COLOR_TEMP

    @property
    def is_on(self):
        """Return true if light is on."""
        # Any brightness means light is on.
        return self._hsbk.b > 0

    @property
    def hs_color(self):
        """Return the hue and saturation color value [float, float]."""
        if self._hsbk.s:
            return (self._hsbk.h, self._hsbk.s)
        return None

    @property
    def brightness(self):
        """Return the brightness of the light."""
        return self._hsbk.b

    @property
    def color_temp(self):
        """Return the CT color value in mireds."""
        # If we got a saturation value, it means that light has
        # a color set and no temperature (temperature requires
        # light to be white, ie s == 0)
        if self._hsbk.s:
            return None
        return color_util.color_temperature_kelvin_to_mired(self._hsbk.k)

    @property
    def max_mireds(self):
        """Return the warmest color_temp that this light supports."""
        return math.ceil(color_util.color_temperature_kelvin_to_mired(2500))

    @property
    def min_mireds(self):
        """Return the coldest color_temp that this light supports."""
        return math.ceil(color_util.color_temperature_kelvin_to_mired(9000))

    async def async_turn_on(self, **kwargs):
        """Instruct the light to turn on."""

        # Grab the current state, and update that so it's consistent.
        h, s, b, k = self._hsbk

        # We're turning on the light, which means that if it was 0, it was
        # previously turned off by the user (see turn_off).
        # So, if user is switching it on, bring it to full light. If it's
        # tweaking the setup (with the light already on), this value will be
        # eventually updated.
        # The flow is: turn off (b=0) -> turn on (b=255) -> change
        # color/brighthess. It's not possible to move from being off to a
        # different colored light.
        if b < 1:
            b = self._turn_on_brightness

        if ATTR_HS_COLOR in kwargs:
            hue, saturation = kwargs[ATTR_HS_COLOR]
            h = hue
            s = saturation
            k = 3500

        if ATTR_BRIGHTNESS in kwargs:
            b = kwargs[ATTR_BRIGHTNESS]

        if ATTR_COLOR_TEMP in kwargs:
            s = 0
            k = math.ceil(color_util.color_temperature_mired_to_kelvin(kwargs[ATTR_COLOR_TEMP]))

        b = brightness_ha_to_photons(b)
        s = saturation_ha_to_photons(s)

        # If the ligth was turned off, we want to power it and start
        # with all zones dimmed down.
        # Note that we're cheating here, we set the whole strip to the same
        # color (brightness 0) so it's faster. In the past we set each zone
        # brightness to 0, but that causes more network traffic.
        async for pkt in self._sender(DeviceMessages.GetPower(), self._reference):
            if pkt | DeviceMessages.StatePower:
                if pkt.payload.level < 1:
                    await self._sender(LightMessages.SetColor(hue=h, saturation=s, brightness=0, kelvin=k), self._reference)
                    await self._sender(DeviceMessages.SetPower(level=65535), self._reference)
                await self._sender(MultiZoneMessages.SetColorZones(start_index=self._zone_start, end_index=self._zone_end, hue=h, saturation=s, brightness=b, kelvin=k), self._reference)

    async def async_turn_off(self, **kwargs):
        """Instruct the light to turn off."""
        h, s, b, k = self._hsbk
        s = saturation_ha_to_photons(s)

        # Set the same HSBK, with a 0 brightness
        await self._sender(MultiZoneMessages.SetColorZones(start_index=self._zone_start, end_index=self._zone_end, hue=h, saturation=s, brightness=0, kelvin=k), self._reference)

        return
        # If the strip has no zones whose brightness is >=0 we can turn the
        # whole strip off.
        zones_lit = list(filter(lambda x: x[2] > 0, self._current_color_zones))
        if len(zones_lit) == 0:
            self._mz_light.set_power(False)

    async def async_update(self):
        """Fetch new state data for this light."""

        hue_values = set()
        saturation_values = set()
        brightness_values = set()
        kelvin_values = set()

        # At this point, we should have a valid light (cached). Use
        # a try block to catch the exception, which means that the
        # device is offline (well, it might mean something else depending
        # on the actual exception, but 99% is that and the only thing we
        # can do is to try the whole thing again anyway).
        try:
            async for pkt in self._sender(MultiZoneMessages.GetColorZones(start_index=self._zone_start, end_index=self._zone_end), self._reference):
                if pkt | MultiZoneMessages.StateMultiZone:
                    # Photons is sending back zones grouped by 8. This means
                    # that we may receive more zones than we requested.
                    last_zone = len(pkt.payload.colors)
                    end_zone = pkt.payload.zone_index + last_zone
                    if end_zone > self._zone_end:
                        last_zone = self._zone_end - pkt.payload.zone_index

                    for zone in pkt.payload.colors[0:last_zone]:
                        hue_values.add(zone.hue)
                        saturation_values.add(zone.saturation)
                        brightness_values.add(zone.brightness)
                        kelvin_values.add(zone.kelvin)
        except:
            _LOGGER.error("Received error while updating color zones. Possibly offline? " + self._mac_address)
            self._available = False
            return


        # Yay!
        self._available = True

        # Reduce the list to a single value. We mostly care
        # about the brightness here, to determine whether the
        # light is on. It's important the list is sorted.
        # For the others, we might get any value.
        h = sorted(list(hue_values))[-1]
        s = saturation_photons_to_ha(sorted(list(saturation_values))[-1])
        b = brightness_photons_to_ha(sorted(list(brightness_values))[-1])
        k = sorted(list(kelvin_values))[-1]

        self._hsbk = HSBK(h, s, b, k)

def brightness_photons_to_ha(value):
    return value * 255

def brightness_ha_to_photons(value):
    return value / 255

def saturation_photons_to_ha(value):
    return value * 100

def saturation_ha_to_photons(value):
    return value / 100
