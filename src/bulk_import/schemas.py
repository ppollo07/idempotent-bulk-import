"""Request and response schemas.

The envelope is validated as a whole: a body that is not ``{"records": [...]}`` with between
1 and 1000 entries is rejected with 422 and nothing is written. Each record is validated
separately, inside the use case, so that one bad record cannot reject the batch (S1).
"""

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_RECORDS = 1000
MAX_EMAIL_LENGTH = 320
MAX_TEXT_LENGTH = 200

Status = Literal["created", "already_existed", "conflict", "failed"]


class BulkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    records: list[dict[str, Any]] = Field(min_length=1, max_length=MAX_RECORDS)


class ClientIn(BaseModel):
    """One client as the migration tool sends it, before validation of its content."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    first_name: str
    last_name: str
    email: str | None = None


class PatientIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    name: str
    species: str
    date_of_birth: date | None = None
    owner_source_id: str


class RecordError(BaseModel):
    code: str
    message: str


class RecordResultOut(BaseModel):
    index: int
    source_id: str | None
    status: Status
    id: str | None = None
    error: RecordError | None = None


class BulkSummary(BaseModel):
    created: int = 0
    already_existed: int = 0
    conflict: int = 0
    failed: int = 0


class BulkResponse(BaseModel):
    summary: BulkSummary
    results: list[RecordResultOut]
