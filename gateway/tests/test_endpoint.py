"""The command endpoint over a real socket: one request in, one answer out."""

import asyncio
import json
from pathlib import Path
from typing import Any

from frostlog_gateway import endpoint


class FakeLink:
    """Answers like the link does, and remembers what it was asked."""

    def __init__(self, answer: dict[str, Any] | None = None) -> None:
        self.asked: list[Any] = []
        self.answer = answer or {"command_id": "c0ffee", "status": "accepted"}

    async def command(self, request: Any) -> dict[str, Any]:
        self.asked.append(request)
        return self.answer


async def _ask(path: Path, request: bytes) -> bytes:
    reader, writer = await asyncio.open_unix_connection(path)
    writer.write(request)
    writer.write_eof()  # a client with nothing more to say; the line is the whole request
    await writer.drain()
    try:
        return await asyncio.wait_for(reader.read(), timeout=5.0)
    finally:
        writer.close()


def _serve(socket_dir: Path, link: FakeLink, *requests: bytes) -> list[bytes]:
    async def run() -> list[bytes]:
        path = socket_dir / "commands.sock"
        server = await endpoint.serve(link.command, path)
        try:
            return [await _ask(path, request) for request in requests]
        finally:
            server.close()
            await server.wait_closed()

    return asyncio.run(run())


def test_one_request_one_answer_and_the_socket_closes(socket_dir: Path) -> None:
    link = FakeLink()
    request = {"setting": "setpoint_celsius", "value": 20, "source": "controller", "reason": "home"}
    (answer,) = _serve(socket_dir, link, json.dumps(request).encode() + b"\n")
    assert link.asked == [request]
    # One line, and then the end of the stream: the client needs no goodbye.
    assert answer.count(b"\n") == 1
    assert json.loads(answer) == {"command_id": "c0ffee", "status": "accepted"}


def test_a_refusal_is_an_answer_too(socket_dir: Path) -> None:
    link = FakeLink({"command_id": "c0ffee", "status": "rejected", "error": "not_connected"})
    (answer,) = _serve(socket_dir, link, b'{"setting":"setpoint_celsius","value":1}\n')
    assert json.loads(answer)["error"] == "not_connected"


def test_a_line_that_is_not_json_reaches_the_link_as_nothing(socket_dir: Path) -> None:
    # The link is the one place that says what a request has to look like, and the
    # one place that puts the attempt on the event stream.
    link = FakeLink({"command_id": "c0ffee", "status": "rejected", "error": "invalid_request"})
    (answer,) = _serve(socket_dir, link, b"not json at all\n")
    assert link.asked == [None]
    assert json.loads(answer)["error"] == "invalid_request"


def test_a_line_that_is_not_even_text_is_refused_the_same_way(socket_dir: Path) -> None:
    # json.loads raises UnicodeDecodeError on bytes that are not UTF-8; that is not a
    # JSONDecodeError, and a client that sent it is still owed an answer.
    link = FakeLink({"command_id": "c0ffee", "status": "rejected", "error": "invalid_request"})
    (answer,) = _serve(socket_dir, link, b'{"setting":"\xff"}\n')
    assert link.asked == [None]
    assert json.loads(answer)["error"] == "invalid_request"


def test_a_client_that_says_nothing_is_not_a_request(socket_dir: Path) -> None:
    link = FakeLink()
    answers = _serve(socket_dir, link, b"\n", b"")
    assert answers == [b"", b""]
    assert link.asked == []


def test_each_request_is_answered_on_its_own_connection(socket_dir: Path) -> None:
    link = FakeLink()
    line = b'{"setting":"setpoint_celsius","value":-20,"source":"c","reason":"away"}\n'
    answers = _serve(socket_dir, link, line, line)
    assert len(link.asked) == 2
    assert [json.loads(answer)["status"] for answer in answers] == ["accepted", "accepted"]
