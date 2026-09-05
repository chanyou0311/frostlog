"""Cooler model name (as used in ``FROSTLOG_COOLER_MODEL``) to implementation.

Implementations are imported lazily so that ``decode`` does not load Bluetooth
libraries and ``read cooler`` does not load anything it does not use.
"""

from frostlog.cooler.base import Decoder, Receiver, Scanner, Sink

MODELS = ("everfrost",)


def _check(model: str) -> None:
    if model not in MODELS:
        raise ValueError(f"unknown cooler model {model!r}; known: {list(MODELS)}")


def create_receiver(
    model: str, sink: Sink, address: str | None, duration: float | None
) -> Receiver:
    _check(model)
    from frostlog.cooler.everfrost.receiver import EverfrostReceiver

    return EverfrostReceiver(sink, address=address, duration=duration)


def create_decoder(model: str) -> Decoder:
    _check(model)
    from frostlog.cooler.everfrost.decoder import EverfrostDecoder

    return EverfrostDecoder()


def scanner(model: str) -> Scanner:
    _check(model)
    from frostlog.cooler.everfrost.ble import scan

    return scan
