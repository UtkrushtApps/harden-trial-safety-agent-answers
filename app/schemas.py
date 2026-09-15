from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class SummaryRequest(BaseModel):
    request_id: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    budget_usd: Decimal = Field(gt=0, le=10)
    reporting_window: str = Field(min_length=1, max_length=120)
    audience: str = Field(default="sponsor-safety-team", max_length=120)


class SummaryResponse(BaseModel):
    run_id: UUID
    study_id: UUID
    summary: str
    status: str
    model_calls: int
    estimated_cost_usd: Decimal
