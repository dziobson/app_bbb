"""Cover module."""
import asyncio
import logging
from typing import Any, Callable
from boneio.const import CLOSE, COVER, IDLE, OPEN, OPENING, CLOSING, CLOSED, STOP
from boneio.helper.events import EventBus
from boneio.helper.mqtt import BasicMqtt
from boneio.relay import MCPRelay

_LOGGER = logging.getLogger(__name__)

COVER_COMMANDS = {
    OPEN: "open_cover",
    CLOSE: "close_cover",
    STOP: "stop",
    "toggle": "toggle",
    "toggle_open": "toggle_open",
}


class RelayHelper:
    """Relay helper for cover either open/close."""

    def __init__(self, relay: MCPRelay, time: int) -> None:
        """Initialize helper."""
        self._relay = relay
        self._steps = 100 / time

    @property
    def relay(self) -> MCPRelay:
        """Get relay."""
        return self._relay

    @property
    def steps(self) -> int:
        """Get steps for each time."""
        return self._steps


class Cover(BasicMqtt):
    """Cover class of boneIO"""

    def __init__(
        self,
        id: str,
        open_relay: Any,
        close_relay: MCPRelay,
        state_save: Callable,
        open_time: int,
        close_time: int,
        event_bus: EventBus,
        restored_state: int = 100,
        **kwargs,
    ) -> None:
        """Initialize cover class."""
        self._loop = asyncio.get_event_loop()
        self._id = id
        super().__init__(id=id, name=id, topic_type=COVER, **kwargs)
        self._lock = asyncio.Lock()
        self._state_save = state_save
        self._open = RelayHelper(relay=open_relay, time=open_time)
        self._close = RelayHelper(relay=close_relay, time=close_time)
        self._set_position = None
        self._current_operation = IDLE
        self._position = restored_state
        self._requested_closing = True
        self._event_bus = event_bus
        self._timer_handle = None
        if self._position is None:
            self._closed = True
        else:
            self._closed = self._position <= 0
        self._event_bus.add_sigterm_listener(self.on_exit)
        self._loop.call_soon_threadsafe(
            self._loop.call_later,
            0.5,
            self.send_state,
        )

    async def run_cover(
        self,
        current_operation: str,
    ) -> None:
        """Run cover engine."""
        if self._current_operation != IDLE:
            self._stop_cover()
        self._current_operation = current_operation

        def get_relays():
            if current_operation == OPENING:
                return (self._open.relay, self._close.relay)
            else:
                return (self._close.relay, self._open.relay)

        (relay, inverted_relay) = get_relays()
        async with self._lock:
            if inverted_relay.is_active:
                inverted_relay.turn_off()
            self._timer_handle = self._event_bus.add_listener(
                f"{COVER}{self.id}", self.listen_cover
            )
            relay.turn_on()

    def on_exit(self) -> None:
        """Stop on exit."""
        self._stop_cover(on_exit=True)

    @property
    def cover_state(self):
        """Current state of cover."""
        return CLOSED if self._closed else OPEN

    def stop(self):
        """Public Stop cover graceful."""
        _LOGGER.info("Stopping cover %s.", self._id)
        if self._current_operation != IDLE:
            self._stop_cover(on_exit=False)

    def send_state(self):
        """Send state of cover to mqtt."""
        self._send_message(topic=f"{self._send_topic}/state", payload=self.cover_state)
        pos = round(self._position, 0)
        self._send_message(topic=f"{self._send_topic}/pos", payload=str(pos))
        self._state_save(position=pos)

    def _stop_cover(self, on_exit=False):
        """Stop cover."""
        self._open.relay.turn_off()
        self._close.relay.turn_off()
        if self._timer_handle is not None:
            self._event_bus.remove_listener(f"{COVER}{self.id}")
            self._timer_handle = None
            self._set_position = None
            if not on_exit:
                self.send_state()
        self._current_operation = IDLE

    @property
    def current_cover_position(self):
        """Return the current position of the cover."""
        return round(self._position, 0)

    def listen_cover(self, *args):
        """Listen for change in cover."""
        if self._current_operation == IDLE:
            return

        def get_step():
            """Get step for current operation."""
            if self._requested_closing:
                return -self._close.steps
            else:
                return self._open.steps

        step = get_step()
        self._position += step
        rounded_pos = round(self._position, 0)
        if self._set_position:
            # Set position is only working for every 10%, so round to nearest 10.
            # Except for start moving time
            if (self._requested_closing and rounded_pos < 95) or rounded_pos > 5:
                rounded_pos = round(self._position, -1)
        else:
            if rounded_pos > 100:
                rounded_pos = 100
            elif rounded_pos < 0:
                rounded_pos = 0
        self._send_message(topic=f"{self._send_topic}/pos", payload=rounded_pos)
        if rounded_pos == self._set_position or (
            self._set_position is None and (rounded_pos >= 100 or rounded_pos <= 0)
        ):
            self._position = rounded_pos
            self._closed = self.current_cover_position <= 0
            self._stop_cover()
            return

        self._closed = self.current_cover_position <= 0

    async def close_cover(self):
        """Close cover."""
        if self._position == 0:
            return
        if self._position is None:
            self._closed = True
            return
        _LOGGER.info("Closing cover %s.", self._id)

        self._requested_closing = True
        self._send_message(topic=f"{self._send_topic}/state", payload=CLOSING)
        await self.run_cover(
            current_operation=CLOSING,
        )

    async def open_cover(self):
        """Open cover."""
        if self._position == 100:
            return
        if self._position is None:
            self._closed = False
            return
        _LOGGER.info("Opening cover %s.", self._id)

        self._requested_closing = False
        self._send_message(topic=f"{self._send_topic}/state", payload=OPENING)
        await self.run_cover(
            current_operation=OPENING,
        )

    async def set_cover_position(self, position: int):
        """Move cover to a specific position."""
        set_position = round(position, -1)
        if self._position == position or set_position == self._set_position:
            return
        if self._set_position:
            self._stop_cover(on_exit=True)
        _LOGGER.info("Setting cover at position %s.", set_position)
        self._set_position = set_position

        self._requested_closing = set_position < self._position
        current_operation = CLOSING if self._requested_closing else OPENING
        _LOGGER.debug(
            "Requested set position %s. Operation %s", set_position, current_operation
        )
        self._send_message(topic=f"{self._send_topic}/state", payload=current_operation)
        await self.run_cover(
            current_operation=current_operation,
        )

    def open(self):
        _LOGGER.debug("Opening cover %s.", self._id)
        asyncio.create_task(self.open_cover())

    def close(self):
        _LOGGER.debug("Closing cover %s.", self._id)
        asyncio.create_task(self.close_cover())

    def toggle(self):
        _LOGGER.debug("Toggle cover %s from input.", self._id)
        if self.cover_state == CLOSED:
            self.close()
        else:
            self.open()

    def toggle_open(self):
        _LOGGER.debug("Toggle open cover %s from input.", self._id)
        if self._current_operation != IDLE:
            self.stop()
        else:
            self.open()

    def toggle_close(self):
        _LOGGER.debug("Toggle close cover %s from input.", self._id)
        if self._current_operation != IDLE:
            self.stop()
        else:
            self.close()
