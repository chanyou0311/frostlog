"""What the resident service tells systemd, and when."""

import socket
from pathlib import Path

import pytest

from frostlog_gateway import service


def test_systemd_is_told_when_the_sockets_are_up(
    monkeypatch: pytest.MonkeyPatch, socket_dir: Path
) -> None:
    path = socket_dir / "notify"
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as manager:
        manager.bind(str(path))
        manager.settimeout(5.0)
        monkeypatch.setenv("NOTIFY_SOCKET", str(path))
        service.notify_ready()
        assert manager.recv(64) == b"READY=1"


def test_nothing_is_said_outside_systemd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    service.notify_ready()  # and nothing is raised either


def test_a_manager_that_is_gone_is_a_warning_not_a_failure(
    monkeypatch: pytest.MonkeyPatch, socket_dir: Path
) -> None:
    monkeypatch.setenv("NOTIFY_SOCKET", str(socket_dir / "nobody-listens"))
    service.notify_ready()
