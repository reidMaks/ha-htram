"""Telemetry over MQTT.

The device publishes a 27-byte payload to ``C/<serial>`` every 30 seconds once
it is provisioned to a broker. This module subscribes through Home Assistant's
own MQTT integration -- no second broker connection, no credentials in our
config flow -- decodes the payload and pushes it into the coordinator.

Enabling this does not create entities. The CO2, temperature and humidity
sensors already exist; they simply stop being fed by Bluetooth polling and
start being fed by whatever arrives here.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from homeassistant.components import mqtt
from homeassistant.core import HomeAssistant, callback

from . import protocol
from .const import TELEMETRY_TOPIC

_LOGGER = logging.getLogger(__name__)


class HtramMqttSource:
    """Subscription to one device's telemetry topic."""

    def __init__(self, hass: HomeAssistant, coordinator, serial: str) -> None:
        """Initialise the source. Nothing is subscribed until started."""
        self.hass = hass
        self.coordinator = coordinator
        self.serial = serial
        self.topic = TELEMETRY_TOPIC.format(serial=serial)
        self._unsubscribe: Callable[[], None] | None = None
        self._undecodable = 0

    async def async_start(self) -> bool:
        """Subscribe to the device's topic.

        Returns False if the MQTT integration never becomes available, which is
        not fatal: Bluetooth polling keeps working and the sensors keep their
        values.
        """
        if not await mqtt.async_wait_for_mqtt_client(self.hass):
            _LOGGER.error(
                "MQTT is not available, so telemetry for %s will not be received. "
                "Bluetooth polling continues",
                self.serial,
            )
            return False

        # encoding=None matters: the payload is binary, and the default utf-8
        # decoding mangles it beyond recognition before it ever reaches us.
        self._unsubscribe = await mqtt.async_subscribe(
            self.hass, self.topic, self._handle_message, qos=0, encoding=None
        )
        _LOGGER.debug("Subscribed to %s", self.topic)
        return True

    @callback
    def async_stop(self) -> None:
        """Unsubscribe. Safe to call when never started."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
            _LOGGER.debug("Unsubscribed from %s", self.topic)

    @callback
    def _handle_message(self, msg: mqtt.ReceiveMessage) -> None:
        """Decode one payload and hand it to the coordinator."""
        payload = msg.payload
        if isinstance(payload, str):  # a mis-set encoding upstream
            payload = payload.encode("utf-8", "surrogateescape")

        reading = protocol.decode_telemetry(payload)
        if reading is None:
            self._undecodable += 1
            # One bad payload is noise; a steady stream of them means the topic
            # carries something else entirely, and that is worth saying once.
            log = _LOGGER.warning if self._undecodable == 10 else _LOGGER.debug
            log(
                "Undecodable payload on %s (%d so far): %s",
                self.topic,
                self._undecodable,
                payload[:32].hex(),
            )
            return

        self.coordinator.async_apply_telemetry(reading)
