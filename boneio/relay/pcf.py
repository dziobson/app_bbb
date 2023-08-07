"""PCF8575 Relay module."""

import logging

from adafruit_pcf8575 import PCF8575, DigitalInOut

from boneio.const import NONE, SWITCH, PCF
from boneio.helper.events import async_track_point_in_time, utcnow
from boneio.relay.basic import BasicRelay

_LOGGER = logging.getLogger(__name__)


class PCFRelay(BasicRelay):
    """Represents PCF Relay output"""

    def __init__(
        self,
        pin: int,
        mcp: PCF8575,
        expander_id: str,
        output_type: str = SWITCH,
        restored_state: bool = False,
        **kwargs
    ) -> None:
        """Initialize MCP relay."""
        self._pin: DigitalInOut = mcp.get_pin(pin)
        if output_type == NONE:
            """Just in case to not restore state of covers etc."""
            restored_state = False
        self._pin.switch_to_output(value=restored_state)
        super().__init__(
            **kwargs, output_type=output_type, restored_state=restored_state
        )
        self._pin_id = pin
        self._expander_id = expander_id
        _LOGGER.debug("Setup PCF with pin %s", self._pin_id)

    @property
    def expander_type(self) -> str:
        """Check expander type."""
        return PCF

    @property
    def pin_id(self) -> str:
        """Return PIN id."""
        return self._pin_id

    @property
    def expander_id(self) -> str:
        """Retrieve parent MCP ID."""
        return self._mcp_id

    @property
    def is_active(self) -> bool:
        """Is relay active."""
        return self.pin.value

    @property
    def pin(self) -> str:
        """PIN of the relay"""
        return self._pin

    def turn_on(self) -> None:
        """Call turn on action."""
        self.pin.value = True
        if self._momentary_turn_on:
            async_track_point_in_time(
                loop=self._loop,
                action=lambda x: self._momentary_callback(time=x, action=self.turn_off),
                point_in_time=utcnow() + self._momentary_turn_on.as_timedelta,
            )
        self._loop.call_soon_threadsafe(self.send_state)

    def turn_off(self) -> None:
        """Call turn off action."""
        self.pin.value = False
        if self._momentary_turn_off:
            async_track_point_in_time(
                loop=self._loop,
                action=lambda x: self._momentary_callback(time=x, action=self.turn_on),
                point_in_time=utcnow() + self._momentary_turn_off.as_timedelta,
            )
        self._loop.call_soon_threadsafe(self.send_state)