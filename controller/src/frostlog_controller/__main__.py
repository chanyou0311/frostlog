"""Entry point: `python3 -m frostlog_controller` -- one run, then exit.

Standard library only (tomllib, socket, subprocess -- all in Python 3.13):
measured on the Pi Zero, importing pydantic and typer alone costs 2.4 s next
to a 0.3 s interpreter start, against a job meant to start and finish quickly,
often.
"""

import logging
import sys

from frostlog_controller import gateway, state
from frostlog_controller.config import ConfigError, load_config
from frostlog_controller.run import run

log = logging.getLogger("frostlog_controller")


def main() -> int:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = load_config()
        socket_path = gateway.socket_path()
    except (ConfigError, gateway.GatewayError) as exc:
        log.error("%s", exc)
        return 2
    run(config, state.state_path(), socket_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
