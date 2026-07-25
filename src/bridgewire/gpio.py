from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from importlib import import_module
from typing import Any

from bridgewire.interfaces import Clock


class RelayActionType(StrEnum):
    SETUP = "setup"
    LOW = "low"
    HIGH = "high"
    CLEANUP = "cleanup"


@dataclass(frozen=True, slots=True)
class RelayAction:
    action: RelayActionType
    timestamp: float
    channel: int


class SimulatedRelay:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self.actions: list[RelayAction] = []
        self.is_high = False
        self.channel: int | None = None
        self.numbering: str | None = None
        self.fail_next_high = False
        self.fail_next_low = False
        self.fail_next_setup = False
        self.fail_next_cleanup = False

    def setup(self, *, numbering: str, channel: int) -> None:
        if self.fail_next_setup:
            self.fail_next_setup = False
            raise RuntimeError("injected setup failure")
        self.numbering = numbering
        self.channel = channel
        self.actions.append(RelayAction(RelayActionType.SETUP, self._clock.monotonic(), channel))

    def command(self, high: bool) -> None:
        if self.channel is None:
            raise RuntimeError("relay has not been set up")
        if high and self.fail_next_high:
            self.fail_next_high = False
            raise RuntimeError("injected HIGH failure")
        if not high and self.fail_next_low:
            self.fail_next_low = False
            raise RuntimeError("injected LOW failure")
        self.is_high = high
        action = RelayActionType.HIGH if high else RelayActionType.LOW
        self.actions.append(RelayAction(action, self._clock.monotonic(), self.channel))

    def cleanup(self) -> None:
        if self.fail_next_cleanup:
            self.fail_next_cleanup = False
            raise RuntimeError("injected cleanup failure")
        if self.channel is None:
            return
        self.actions.append(
            RelayAction(RelayActionType.CLEANUP, self._clock.monotonic(), self.channel)
        )


class RaspberryPiRelay:
    """Fail-safe RPi.GPIO adapter; import is lazy to preserve backend isolation."""

    def __init__(self, gpio: object | None = None) -> None:
        if gpio is None:
            gpio = import_module("RPi.GPIO")
        self._gpio: Any = gpio
        self._channel: int | None = None
        self._closed = False

    def setup(self, *, numbering: str, channel: int) -> None:
        if self._channel is not None:
            return
        if numbering != "BCM" or channel != 23:
            raise ValueError("physical relay requires BCM channel 23")
        gpio = self._gpio
        gpio.setwarnings(False)
        gpio.setmode(gpio.BCM)
        gpio.setup(channel, gpio.OUT, initial=gpio.LOW)
        self._channel = channel
        self._closed = False

    def command(self, high: bool) -> None:
        if self._channel is None or self._closed:
            raise RuntimeError("relay has not been set up")
        gpio = self._gpio
        gpio.output(self._channel, gpio.HIGH if high else gpio.LOW)

    def cleanup(self) -> None:
        if self._channel is None or self._closed:
            return
        gpio = self._gpio
        channel = self._channel
        try:
            gpio.output(channel, gpio.LOW)
        finally:
            gpio.cleanup(channel)
            self._closed = True
