import asyncio
import logging
from itertools import cycle
from typing import List

from luma.core.interface.serial import i2c
from luma.core.render import canvas
from luma.oled.device import sh1106
from luma.core.error import DeviceNotFoundError
from PIL import ImageDraw

from boneio.const import CPU, DISK, MEMORY, NETWORK, OLED_PIN, SWAP, UPTIME, WHITE
from boneio.helper import (
    HostData,
    configure_pin,
    edge_detect,
    make_font,
    setup_input,
    I2CError,
)

_LOGGER = logging.getLogger(__name__)


fontBig = make_font("DejaVuSans.ttf", 12)
fontSmall = make_font("DejaVuSans.ttf", 9)
fontExtraSmall = make_font("DejaVuSans.ttf", 7)
danube = make_font("danube__.ttf", 15, local=True)

screen_order = [UPTIME, NETWORK, CPU, DISK, MEMORY, SWAP]

STANDARD_ROWS = [17, 32, 47]
UPTIME_ROWS = list(range(22, 60, 10))
OUTPUT_ROWS = list(range(14, 60, 6))
OUTPUT_COLS = range(0, 113, 56)


class Oled:
    """Oled display class."""

    def __init__(
        self, host_data: HostData, output_groups: List[str], sleep_timeout: int
    ) -> None:
        """Initialize OLED screen."""
        self._loop = asyncio.get_running_loop()
        self._screen_order = cycle(screen_order + output_groups)
        self._output_groups = output_groups
        self._current_screen = next(self._screen_order)
        self._host_data = host_data
        self._sleep = False
        self._sleep_handle = None
        self._sleep_timeout = sleep_timeout
        configure_pin(OLED_PIN)
        setup_input(OLED_PIN)
        edge_detect(pin=OLED_PIN, callback=self._handle_press, bounce=120)
        try:
            serial = i2c(port=2, address=0x3C)
            self._device = sh1106(serial)
        except DeviceNotFoundError as err:
            raise I2CError(err)
        _LOGGER.debug("Configuring OLED screen.")

    def _draw_standard(self, data: dict, draw: ImageDraw) -> None:
        """Draw standard information about host screen."""
        draw.text((1, 1), self._current_screen, font=fontBig, fill=WHITE)
        i = 0
        for k in data:
            draw.text(
                (3, STANDARD_ROWS[i]),
                f"{k} {data[k]}",
                font=fontSmall,
                fill=WHITE,
            )
            i += 1

    def _sleeptime(self):
        with canvas(self._device) as draw:
            draw.rectangle(self._device.bounding_box, outline="black", fill="black")
        self._sleep = True

    def _draw_uptime(self, data: dict, draw: ImageDraw) -> None:
        """Draw uptime screen with boneIO logo."""
        draw.text((3, 3), "bone", font=danube, fill=WHITE)
        draw.text((53, 3), "iO", font=danube, fill=WHITE)
        i = 0
        for k in data:
            draw.text(
                (3, UPTIME_ROWS[i]),
                f"{k}: {data[k]}",
                font=fontSmall,
                fill=WHITE,
            )
            i += 1

    def _draw_output(self, data: dict, draw: ImageDraw) -> None:
        "Draw outputs of GPIO/MCP relays."
        cols = cycle(OUTPUT_COLS)
        draw.text((1, 1), f"Relay {self._current_screen}", font=fontSmall, fill=WHITE)
        i = 0
        j = next(cols)
        for k in data:
            if len(OUTPUT_ROWS) == i:
                j = next(cols)
                i = 0
            draw.text(
                (j, OUTPUT_ROWS[i]), f"{k} {data[k]}", font=fontExtraSmall, fill=WHITE
            )
            i += 1

    def render_display(self) -> None:
        """Render display."""
        data = self._host_data.get(self._current_screen)
        if data:
            with canvas(self._device) as draw:
                if self._current_screen in self._output_groups:
                    self._draw_output(data, draw)
                elif self._current_screen == UPTIME:
                    self._draw_uptime(data, draw)
                else:
                    self._draw_standard(data, draw)
        if not self._sleep_handle and self._sleep_timeout > 0:
            self._sleep_handle = self._loop.call_soon_threadsafe(
                self._loop.call_later,
                self._sleep_timeout,
                self._sleeptime,
            )

    def handle_data_update(self, type: str):
        """Callback to handle new data present into screen."""
        if type == self._current_screen and not self._sleep:
            self.render_display()

    def _handle_press(self, pin: any) -> None:
        """Handle press of PIN for OLED display."""
        if self._sleep_handle:
            self._sleep_handle.cancel()
            self._sleep_handle = None
        if not self._sleep:
            self._current_screen = next(self._screen_order)
        else:
            self._sleep = False
        self.render_display()
