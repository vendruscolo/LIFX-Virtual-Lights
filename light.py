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
from photons_app.special import HardCodedSerials
from photons_control.multizone import SetZones
from photons_messages import DeviceMessages
from photons_messages import LightMessages
from photons_messages import MultiZoneMessages
from photons_messages import MultiZoneEffectType
from photons_messages import protocol_register
from photons_transport.targets import LanTarget

from .const import (
    DOMAIN,
    CONF_NAME,
    CONF_TARGET_LIGHT,
    CONF_ZONE_START,
    CONF_ZONE_END,
    CONF_TURN_ON_BRIGHTNESS,
    CONF_TURN_ON_DURATION,
    CONF_TURN_OFF_DURATION
)

_LOGGER = logging.getLogger(__name__)

FIND_TIMEOUT = 4
SCAN_INTERVAL = timedelta(seconds=FIND_TIMEOUT + 1)

# Validation of the user's configuration
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_NAME): cv.string,
    vol.Required(CONF_TARGET_LIGHT ): cv.string,
    vol.Required(CONF_ZONE_START): vol.Coerce(int),
    vol.Required(CONF_ZONE_END): vol.Coerce(int),
    vol.Optional(CONF_TURN_ON_BRIGHTNESS, default=255): vol.Coerce(int),
    vol.Optional(CONF_TURN_ON_DURATION, default=0.5): vol.Coerce(float),
    vol.Optional(CONF_TURN_OFF_DURATION, default=0.5): vol.Coerce(float),
})

HSBK = namedtuple('HSBK', ['h', 's', 'b', 'k'])

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    # Assign configuration variables.
    # The configuration check takes care they are present.

    name = config[CONF_NAME]
    mac_address = config[CONF_TARGET_LIGHT]
    zone_start = config[CONF_ZONE_START]
    zone_end = config[CONF_ZONE_END]
    turn_on_brightness = config[CONF_TURN_ON_BRIGHTNESS]
    turn_on_duration = config[CONF_TURN_ON_DURATION]
    turn_off_duration = config[CONF_TURN_OFF_DURATION]

    # Verify that passed in configuration works
    if zone_end < zone_start:
        _LOGGER.error("Zone end must be greater than or equal to zone start")
        return

    entity = await LIFXVirtualLight.create(mac_address, name, zone_start, zone_end, turn_on_brightness, turn_on_duration, turn_off_duration)
    async_add_entities([entity])

class LightMiddleware:
    target = LanTarget.create({"protocol_register": protocol_register, "final_future": asyncio.Future()})
    sender_instances = {}
    zones_data = {}
    zones_data_timestamps = {}
    zones_updating = {}

    @classmethod
    async def create(cls, mac_address, zone_start, zone_end):
        self = cls(mac_address, zone_start, zone_end)

        if mac_address not in LightMiddleware.sender_instances:
            sender = await LightMiddleware.target.make_sender()
            LightMiddleware.sender_instances[mac_address] = sender

        self._sender = LightMiddleware.sender_instances[mac_address]

        return self

    def __init__(self, mac_address, zone_start, zone_end):
        self._mac_address = mac_address
        self._zone_start = zone_start
        self._zone_end = zone_end
        self._available = False # Assume not available until we get an update

        if mac_address not in LightMiddleware.zones_data:
            LightMiddleware.zones_data[mac_address] = []

        if mac_address not in LightMiddleware.zones_data_timestamps:
            LightMiddleware.zones_data_timestamps[mac_address] = time.time()

        if mac_address not in LightMiddleware.zones_updating:
            LightMiddleware.zones_updating[mac_address] = False

    @property
    def unique_id(self):
        """Return the unique id of this light."""
        return self._mac_address + "|" + str(self._zone_start) + "|" + str(self._zone_end)

    async def update(self):
        diff = time.time() - LightMiddleware.zones_data_timestamps[self._mac_address]
        is_updating = LightMiddleware.zones_updating[self._mac_address]

        if diff < SCAN_INTERVAL.total_seconds() or is_updating:
            return LightMiddleware.zones_data[self._mac_address][self._zone_start:self._zone_end]

        LightMiddleware.zones_updating[self._mac_address] = True

        # This will request all zones in the light strip, even those that
        # are outside of the virtual light. Make sure to only read info
        # for the correct zone range.
        plans = self._sender.make_plans("zones")
        async for _, _, info in self._sender.gatherer.gather(plans, self._mac_address, find_timeout=FIND_TIMEOUT, error_catcher=self.error_catcher):
            if info is not self._sender.gatherer.Skip:
                self._available = True
                zones = [z for _, z in sorted(info)]
                LightMiddleware.zones_data[self._mac_address] = zones
                LightMiddleware.zones_data_timestamps[self._mac_address] = time.time()

        LightMiddleware.zones_updating[self._mac_address] = False

        return LightMiddleware.zones_data[self._mac_address][self._zone_start:self._zone_end]

    async def turn_on(self, h, s, b, k, duration):
        LightMiddleware.zones_updating[self._mac_address] = True

        # If the ligth was turned off, we want to power it and start
        # with all zones dimmed down.
        # Note that we're cheating here, we set the whole strip to the same
        # color (brightness 0) so it's faster. In the past we set each zone
        # brightness to 0, but that causes more network traffic.
        await self.async_stop_effects()
        async for pkt in self._sender(DeviceMessages.GetPower(), self._mac_address, find_timeout=FIND_TIMEOUT):
            if pkt | DeviceMessages.StatePower:
                if pkt.payload.level < 1:
                    await self._sender(LightMessages.SetColor(hue=h, saturation=s, brightness=0, kelvin=k), self._mac_address, find_timeout=FIND_TIMEOUT)
                    await self._sender(DeviceMessages.SetPower(level=65535), self._mac_address, find_timeout=FIND_TIMEOUT)

                await self._sender(SetZones([[{"hue": h, "saturation": s, "brightness": b, "kelvin": k}, self._zone_end - self._zone_start + 1]], zone_index=self._zone_start, duration=duration), self._mac_address, find_timeout=FIND_TIMEOUT)

        LightMiddleware.zones_updating[self._mac_address] = False

    async def turn_off(self, h, s, k, duration):
        LightMiddleware.zones_updating[self._mac_address] = True

        await self.async_stop_effects()

        # Set the same HSBK, with a 0 brightness
        await self._sender(SetZones([[{"hue": h, "saturation": s, "brightness": 0, "kelvin": k}, self._zone_end - self._zone_start + 1]], zone_index=self._zone_start, duration=duration), self._mac_address, find_timeout=FIND_TIMEOUT)

        # At this point our zones are dark, we want to turn the whole strip
        # off if there's no zone lit. Get the full zones, and if there are
        # no zones lit, turn the whole thing off.
        # (This will request all zones in the light strip, even those that
        # are outside of the virtual light.)
        any_zone_lit = False
        plans = self._sender.make_plans("zones")
        async for _, _, info in self._sender.gatherer.gather(plans, self._mac_address, find_timeout=FIND_TIMEOUT, error_catcher=self.error_catcher):
            if info is not self._sender.gatherer.Skip:
                self._available = True
                zones = [z for _, z in sorted(info)]
                LightMiddleware.zones_data[self._mac_address] = zones
                LightMiddleware.zones_data_timestamps[self._mac_address] = time.time()
                for zone in zones:
                    if zone.brightness:
                        any_zone_lit = True

        if any_zone_lit == False:
            await self._sender(DeviceMessages.SetPower(level=0), self._mac_address, find_timeout=FIND_TIMEOUT)

        LightMiddleware.zones_updating[self._mac_address] = False

    async def async_stop_effects(self):
        await self._sender(MultiZoneMessages.SetMultiZoneEffect(type=MultiZoneEffectType.OFF), self._mac_address, find_timeout=FIND_TIMEOUT)

    def error_catcher(self, error):
        # We got an error. Disable this entity, and hope it'll come
        # back later. Exceptions here are usually timeouts (device was
        # working and went offline after HA started) or device isn't
        # available at all (it was never discovered).
        self._available = False
        _LOGGER.error(f"Received error while updating color zones for {self._mac_address}. Possibly offline? Error: {error}")

class LIFXVirtualLight(LightEntity):
    @classmethod
    async def create(cls, mac_address, name, zone_start, zone_end, turn_on_brightness, turn_on_duration, turn_off_duration):
        self = cls(mac_address, name, zone_start, zone_end, turn_on_brightness, turn_on_duration, turn_off_duration)
        self._middleware = await LightMiddleware.create(mac_address, zone_start, zone_end)
        return self

    def __init__(self, mac_address, name, zone_start, zone_end, turn_on_brightness, turn_on_duration, turn_off_duration):
        """Initialize a Virtual Light."""

        # Conf
        self._name = name
        self._turn_on_brightness = turn_on_brightness
        self._turn_on_duration = turn_on_duration
        self._turn_off_duration = turn_off_duration

        # Cached values
        self._hsbk = HSBK(0, 0, 0, 0)

    @property
    def name(self):
        """Return the display name of this light."""
        return self._name

    @property
    def unique_id(self):
        """Return the unique id of this light."""
        return self._middleware.unique_id

    @property
    def available(self):
        """Indicate if Home Assistant is able to read the state and control the underlying device."""
        return self._middleware._available

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

        await self._middleware.turn_on(h, s, b, k, self._turn_on_duration)


    async def async_turn_off(self, **kwargs):
        """Instruct the light to turn off."""
        h, s, b, k = self._hsbk
        s = saturation_ha_to_photons(s)

        await self._middleware.turn_off(h, s, k, self._turn_off_duration)

    async def async_update(self):
        """Fetch new state data for this light."""

        h = 0
        s = 0
        b = 0
        k = 0

        zones = await self._middleware.update()
        for zone in zones:
            h = max(h, zone.hue)
            s = max(s, zone.saturation)
            b = max(b, zone.brightness)
            k = max(k, zone.kelvin)

        self._hsbk = HSBK(h, saturation_photons_to_ha(s), brightness_photons_to_ha(b), k)

def brightness_photons_to_ha(value):
    return value * 255

def brightness_ha_to_photons(value):
    return value / 255

def saturation_photons_to_ha(value):
    return value * 100

def saturation_ha_to_photons(value):
    return value / 100
