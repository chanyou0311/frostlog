from datetime import UTC, date, datetime
from typing import Any

import pytest
from google.api_core import exceptions

from frostlog_notifier.errors import Transient
from frostlog_notifier.warehouse import BigQueryWarehouse


class FakeClient:
    def __init__(self, rows: list[Any] | None = None, error: Exception | None = None) -> None:
        self.rows = rows or []
        self.error = error
        self.jobs: list[tuple[str, Any]] = []

    def query(self, sql: str, job_config: Any = None) -> Any:
        self.jobs.append((sql, job_config))
        if self.error is not None:
            raise self.error
        return self

    def result(self) -> list[Any]:
        return self.rows


def warehouse(client: FakeClient) -> BigQueryWarehouse:
    return BigQueryWarehouse("frostlog-chanyou", "frostlog", client=client)


def test_a_table_is_named_by_project_and_dataset() -> None:
    assert (
        warehouse(FakeClient()).table("fact_cooler_pulldown")
        == "`frostlog-chanyou.frostlog.fact_cooler_pulldown`"
    )


def test_parameters_are_typed_after_their_values() -> None:
    client = FakeClient()
    warehouse(client).rows(
        "SELECT 1",
        {
            "flag": True,
            "count": 3,
            "ratio": 0.5,
            "name": "x",
            "moment": datetime(2026, 9, 11, tzinfo=UTC),
            "day": date(2026, 9, 11),
        },
    )
    _, config = client.jobs[0]
    assert [(parameter.name, parameter.type_) for parameter in config.query_parameters] == [
        ("flag", "BOOL"),
        ("count", "INT64"),
        ("ratio", "FLOAT64"),
        ("name", "STRING"),
        ("moment", "TIMESTAMP"),
        ("day", "DATE"),
    ]


def test_a_parameter_of_an_unknown_type_is_refused() -> None:
    with pytest.raises(TypeError):
        warehouse(FakeClient()).rows("SELECT 1", {"weird": object()})


def test_rows_come_back_as_plain_dictionaries() -> None:
    client = FakeClient(rows=[{"state_of_charge_percent": 62}])
    assert warehouse(client).rows("SELECT 1") == [{"state_of_charge_percent": 62}]


def test_bigquery_being_unavailable_is_transient() -> None:
    client = FakeClient(error=exceptions.ServiceUnavailable("down"))
    with pytest.raises(Transient):
        warehouse(client).rows("SELECT 1")


def test_a_bad_query_is_not_transient() -> None:
    client = FakeClient(error=exceptions.BadRequest("syntax error"))
    with pytest.raises(exceptions.BadRequest):
        warehouse(client).execute("SELECT 1")
