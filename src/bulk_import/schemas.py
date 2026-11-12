"""Request and response schemas.

The envelope is validated as a whole: a body that is not ``{"records": [...]}`` with between
1 and 1000 entries is rejected with 422 and nothing is written. Each record's *fields* are
validated separately, inside the use case, so that one bad field cannot reject the batch (S1).

One boundary is deliberately not per-record: an element that is not an object at all (``null``,
``"C-1"``, ``42``) matches neither branch of the union below, so it is an envelope error and the
batch is rejected. Per-record outcomes need a record to report on.
"""

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_RECORDS = 1000
MAX_EMAIL_LENGTH = 320
MAX_TEXT_LENGTH = 200

Status = Literal["created", "already_existed", "conflict", "failed"]


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


class ClientsRequest(BaseModel):
    """The envelope for ``/clients/bulk``.

    ``records`` is a list of loose objects that *document* a ``ClientIn``, but are not validated
    as one here, on purpose: a record whose fields do not parse must become a per-record
    ``failed``, never a 422 for the whole batch — except when the element is not an object, which
    the union cannot accept and which therefore rejects the envelope. The union keeps the fields
    visible in ``/docs`` without paying the per-field cost.
    """

    model_config = ConfigDict(extra="forbid")

    records: list[dict[str, Any] | ClientIn] = Field(
        min_length=1,
        max_length=MAX_RECORDS,
        examples=[
            [
                {
                    "source_id": "C-1",
                    "first_name": "Ana",
                    "last_name": "Ruiz",
                    "email": "ana@example.com",
                }
            ]
        ],
    )


class PatientsRequest(BaseModel):
    """The envelope for ``/patients/bulk``. Same contract as ``ClientsRequest``."""

    model_config = ConfigDict(extra="forbid")

    records: list[dict[str, Any] | PatientIn] = Field(
        min_length=1,
        max_length=MAX_RECORDS,
        examples=[
            [
                {
                    "source_id": "P-1",
                    "name": "Luna",
                    "species": "dog",
                    "date_of_birth": "2020-01-02",
                    "owner_source_id": "C-1",
                }
            ]
        ],
    )


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
