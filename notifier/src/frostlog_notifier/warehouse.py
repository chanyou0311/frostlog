"""Access to the BigQuery dataset holding the semantic data product.

Everything above this module speaks the :class:`Warehouse` protocol, so the
tests run the real queries against a fake that answers by statement name.
"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, Protocol

from frostlog_notifier.errors import Transient

Parameters = Mapping[str, Any]
Row = Mapping[str, Any]


class Warehouse(Protocol):
    def table(self, name: str) -> str:
        """The name to write in a statement's FROM clause."""

    def rows(self, sql: str, parameters: Parameters | None = None) -> list[Row]:
        """Run a query and read all of its rows."""

    def execute(self, sql: str, parameters: Parameters | None = None) -> None:
        """Run a statement that returns nothing (DDL, INSERT)."""


class BigQueryWarehouse:
    """A :class:`Warehouse` backed by a BigQuery dataset."""

    def __init__(self, project: str, dataset: str, client: Any = None) -> None:
        self._project = project
        self._dataset = dataset
        self._client = client
        self._parameter_types = {
            bool: "BOOL",
            int: "INT64",
            float: "FLOAT64",
            str: "STRING",
            datetime: "TIMESTAMP",
            date: "DATE",
        }

    @property
    def client(self) -> Any:
        if self._client is None:
            from google.cloud import bigquery

            self._client = bigquery.Client(project=self._project)
        return self._client

    def table(self, name: str) -> str:
        return f"`{self._project}.{self._dataset}.{name}`"

    def rows(self, sql: str, parameters: Parameters | None = None) -> list[Row]:
        return [dict(row) for row in self._run(sql, parameters)]

    def execute(self, sql: str, parameters: Parameters | None = None) -> None:
        self._run(sql, parameters)

    def _run(self, sql: str, parameters: Parameters | None) -> Sequence[Any]:
        from google.api_core import exceptions
        from google.cloud import bigquery

        config = bigquery.QueryJobConfig(query_parameters=self._query_parameters(parameters or {}))
        try:
            return list(self.client.query(sql, job_config=config).result())
        except (exceptions.ServerError, exceptions.TooManyRequests, exceptions.RetryError) as exc:
            raise Transient(f"BigQuery is unavailable: {exc}") from exc

    def _query_parameters(self, parameters: Parameters) -> list[Any]:
        from google.cloud import bigquery

        given = []
        for name, value in parameters.items():
            if isinstance(value, list):
                # The only arrays passed are lists of keys.
                given.append(bigquery.ArrayQueryParameter(name, "STRING", [str(v) for v in value]))
                continue
            # bool before int: bool is a subclass of int and BOOL is the narrower type.
            kind = next(
                (t for t in self._parameter_types if isinstance(value, t)),
                None,
            )
            if kind is None:
                raise TypeError(f"parameter {name} has unsupported type {type(value).__name__}")
            given.append(bigquery.ScalarQueryParameter(name, self._parameter_types[kind], value))
        return given
