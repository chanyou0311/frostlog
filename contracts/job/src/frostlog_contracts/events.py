"""What the contract test says when it is done, as contracts/signals.odcs.yaml declares it."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class QualityReport(BaseModel):
    """Published once per contract by the daily contract test run."""

    #: The topic's discriminator: consumers branch on this, not on field presence.
    event: Literal["quality_report"] = "quality_report"
    run_id: str
    published_at: datetime
    contract_id: str
    passed: bool
    failed_checks: list[str]

    @property
    def name(self) -> str:
        return "quality_report"
