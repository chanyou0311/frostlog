from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_events import QUALITY_REPORT, SEMANTIC_UPDATED, envelope

from frostlog_notifier.app import app, get_notifier
from frostlog_notifier.errors import Transient
from frostlog_notifier.notification import Notification


class StubNotifier:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.handled: list[Any] = []
        self.deadlines = 0
        self.reported: list[tuple[str, BaseException]] = []

    def handle(self, event: Any) -> list[Notification]:
        if self.error is not None:
            raise self.error
        self.handled.append(event)
        return [Notification(kind="homecoming", key="2026-09-11T12:03:00+00:00", text="x")]

    def run_weekly_deadline(self) -> list[Notification]:
        if self.error is not None:
            raise self.error
        self.deadlines += 1
        return [Notification(kind="weekly", key="2026-W37", text="x")]

    def report_failure(self, context: str, error: BaseException) -> None:
        self.reported.append((context, error))


@pytest.fixture
def notifier() -> Iterator[StubNotifier]:
    stub = StubNotifier()
    app.dependency_overrides[get_notifier] = lambda: stub
    yield stub
    app.dependency_overrides.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_an_event_is_handled_and_acknowledged(client: TestClient, notifier: StubNotifier) -> None:
    response = client.post("/events/semantic", json=envelope(SEMANTIC_UPDATED))
    assert response.status_code == 200
    assert response.json()["posted"] == ["2026-09-11T12:03:00+00:00"]
    assert len(notifier.handled) == 1


def test_a_quality_report_reaches_the_notifier(client: TestClient, notifier: StubNotifier) -> None:
    assert client.post("/events/semantic", json=envelope(QUALITY_REPORT)).status_code == 200
    assert len(notifier.handled) == 1


@pytest.mark.parametrize("body", [{"nothing": "useful"}, {"message": {"data": "!!!"}}])
def test_a_message_that_cannot_be_read_is_acknowledged(
    client: TestClient, notifier: StubNotifier, body: dict[str, Any]
) -> None:
    response = client.post("/events/semantic", json=body)
    assert response.status_code == 200
    assert "dropped" in response.json()
    assert notifier.handled == []


def test_a_transient_failure_asks_for_a_retry(client: TestClient) -> None:
    stub = StubNotifier(error=Transient("BigQuery is unavailable"))
    app.dependency_overrides[get_notifier] = lambda: stub
    try:
        response = client.post("/events/semantic", json=envelope(SEMANTIC_UPDATED))
        assert response.status_code == 500
        assert stub.reported == []
    finally:
        app.dependency_overrides.clear()


def test_a_defect_is_reported_and_acknowledged(client: TestClient) -> None:
    stub = StubNotifier(error=ValueError("boom"))
    app.dependency_overrides[get_notifier] = lambda: stub
    try:
        response = client.post("/events/semantic", json=envelope(SEMANTIC_UPDATED))
        # Retrying would only repeat the defect; take the message off the queue.
        assert response.status_code == 200
        assert stub.reported[0][0] == "events/semantic"
    finally:
        app.dependency_overrides.clear()


def test_the_monday_job_runs(client: TestClient, notifier: StubNotifier) -> None:
    response = client.post("/jobs/weekly-deadline")
    assert response.status_code == 200
    assert response.json()["posted"] == ["2026-W37"]
    assert notifier.deadlines == 1


def test_the_monday_job_reports_a_failure_and_asks_to_be_run_again(client: TestClient) -> None:
    stub = StubNotifier(error=ValueError("boom"))
    app.dependency_overrides[get_notifier] = lambda: stub
    try:
        assert client.post("/jobs/weekly-deadline").status_code == 500
        assert stub.reported[0][0] == "jobs/weekly-deadline"
    finally:
        app.dependency_overrides.clear()


def test_the_service_answers_a_health_check(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
