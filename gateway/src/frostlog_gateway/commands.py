"""What may be written to the cooler, and how it goes on the wire.

The cooler takes many settings; this allows one. A command that is not on the
list is refused here rather than anywhere else, so that "what a gateway client
can change" is answered by reading one file. Widening it is a decision, not a
configuration: the cooler is in a car, and a client that can write anything can
also flatten the battery.

The wire form was read off the Anker app's session and confirmed on the cooler on
2026-09-14: a setpoint write is acknowledged in a tenth of a second and shows up
in the next state report, including when the value written is the one already
set. The value travels in whatever unit the cooler is displaying -- writing 10
while it shows °F sets it to 10 °F -- which is why a command is refused until a
state report has said which unit that is.
"""

from dataclasses import dataclass
from typing import Any

from frostlog_gateway.decoder import to_display
from frostlog_gateway.handshake import Cipher
from frostlog_gateway.protocol import (
    PATTERN_COMMAND,
    Parameter,
    build_frame,
    build_parameters,
)

CMD_SETPOINT = bytes.fromhex("4080")
CMD_STATE_REQUEST = bytes.fromhex("4040")

SETTING_SETPOINT = "setpoint_celsius"
#: The range the cooler's own panel offers.
SETPOINT_CELSIUS = (-20, 20)


class Rejected(Exception):
    """A command the gateway will not send. ``reason`` is what the client is told."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Command:
    """One accepted request: what to set, and how to tell that it was set."""

    setting: str
    celsius: int
    cmd: bytes = CMD_SETPOINT

    @property
    def ack_cmd(self) -> str:
        """The cmd the cooler acknowledges with: the request's, with bit 0x0800 set."""
        return f"{int.from_bytes(self.cmd, 'big') | 0x0800:04x}"

    def frame(self, cipher: Cipher, display_unit: str, timestamp: int) -> bytes:
        value = to_display(self.celsius, display_unit)
        return _frame(
            cipher,
            self.cmd,
            [
                Parameter(0xA1, None, b"\x21"),
                Parameter(0xA3, 0x01, value.to_bytes(1, "little", signed=True)),
                Parameter(0xFE, 0x03, timestamp.to_bytes(4, "little")),
            ],
        )

    def applied(self, state: dict[str, Any]) -> bool:
        """Whether a decoded state report shows what was asked for."""
        return state.get(self.setting) == self.celsius


def parse(setting: Any, value: Any) -> Command:
    """The command for a request, or :class:`Rejected` saying why there is none."""
    if setting != SETTING_SETPOINT:
        raise Rejected("unknown_setting")
    celsius = _integer(value)
    low, high = SETPOINT_CELSIUS
    if not low <= celsius <= high:
        raise Rejected("value_out_of_range")
    return Command(setting=SETTING_SETPOINT, celsius=celsius)


def state_request(cipher: Cipher, timestamp: int) -> bytes:
    """Ask the cooler for its state. It answers with a state report's body under cmd 4840.

    The report it sends by itself on connecting is not reliable -- on 2026-09-14 none
    came for five minutes -- and the gateway cannot encode a setpoint before it knows
    the display unit.
    """
    return _frame(
        cipher,
        CMD_STATE_REQUEST,
        [Parameter(0xA1, None, b"\x21"), Parameter(0xFE, 0x03, timestamp.to_bytes(4, "little"))],
    )


def _frame(cipher: Cipher, cmd: bytes, parameters: list[Parameter]) -> bytes:
    """One command frame, encrypted with the session's cipher like every other."""
    return build_frame(PATTERN_COMMAND, cmd, cipher.encrypt(build_parameters(parameters)))


def _integer(value: Any) -> int:
    """A whole number, however the client wrote it; anything else is a malformed request."""
    if isinstance(value, bool):
        raise Rejected("invalid_request")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            raise Rejected("invalid_request") from None
    raise Rejected("invalid_request")
