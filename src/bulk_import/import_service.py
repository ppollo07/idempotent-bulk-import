"""The import use cases: one short transaction per record, one outcome per record.

The idempotency guarantee is the ``UNIQUE (source_id)`` constraint plus
``INSERT ... ON CONFLICT (source_id) DO NOTHING``. Nothing here checks first and inserts
later: a check-then-insert races between two workers, a constraint cannot (D1).

Two consequences worth knowing while reading this file:

* ``ON CONFLICT DO NOTHING`` returns no row for a record that already exists, so a second
  ``SELECT`` fetches the committed row. It runs in ``READ COMMITTED`` (the psycopg default),
  where each statement takes a fresh snapshot: the row committed by the other worker a moment
  ago is visible. Under ``REPEATABLE READ`` that snapshot would be older and the row could be
  missed.
* Each record gets its own transaction, so a failed record cannot roll back the batch and two
  workers sending the same batch in a different order cannot deadlock on it (D3).
"""

import logging
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Any

from psycopg import Connection, errors
from psycopg_pool import ConnectionPool
from pydantic import ValidationError

from .normalize import normalize_client, normalize_patient, payload_hash
from .schemas import (
    MAX_EMAIL_LENGTH,
    MAX_TEXT_LENGTH,
    ClientIn,
    PatientIn,
    Status,
)

logger = logging.getLogger(__name__)

CLIENT_LIMITS = {
    "source_id": MAX_TEXT_LENGTH,
    "first_name": MAX_TEXT_LENGTH,
    "last_name": MAX_TEXT_LENGTH,
    "email": MAX_EMAIL_LENGTH,
}
PATIENT_LIMITS = {
    "source_id": MAX_TEXT_LENGTH,
    "name": MAX_TEXT_LENGTH,
    "species": MAX_TEXT_LENGTH,
    "owner_source_id": MAX_TEXT_LENGTH,
}


@dataclass(frozen=True)
class RecordResult:
    index: int
    source_id: str | None
    status: Status
    id: str | None = None
    error: dict[str, str] | None = None


def import_clients(pool: ConnectionPool, records: list[dict[str, Any]]) -> list[RecordResult]:
    results = [_import_client(pool, index, raw) for index, raw in enumerate(records)]
    _log_batch("clients", results)
    return results


def import_patients(pool: ConnectionPool, records: list[dict[str, Any]]) -> list[RecordResult]:
    results = [_import_patient(pool, index, raw) for index, raw in enumerate(records)]
    _log_batch("patients", results)
    return results


def _log_batch(entity: str, results: list[RecordResult]) -> None:
    """One line per batch for the operator, one line per rejected record for the triage.

    The response is for the caller. The log is for whoever has to answer "where did those 300
    patients go?" a week later, when the response is long gone.
    """
    counts = Counter(result.status for result in results)
    logger.info("%s batch of %d: %s", entity, len(results), dict(counts))
    for result in results:
        if result.status in ("failed", "conflict") and result.error is not None:
            logger.warning(
                "%s record %s: %s (%s)",
                entity,
                result.source_id,
                result.error["code"],
                result.error["message"],
            )


def _import_client(pool: ConnectionPool, index: int, raw: Any) -> RecordResult:
    try:
        validated = ClientIn.model_validate(raw)
    except ValidationError as exc:
        return _failed(index, _raw_source_id(raw), "validation_error", _first_message(exc))

    fields = normalize_client(validated.model_dump())
    problem = _problem(fields, CLIENT_LIMITS, required=("source_id", "first_name", "last_name"))
    if problem is not None:
        return _failed(index, fields["source_id"] or None, "validation_error", problem)

    return _insert_client(pool, index, fields)


def _import_patient(pool: ConnectionPool, index: int, raw: Any) -> RecordResult:
    try:
        validated = PatientIn.model_validate(raw)
    except ValidationError as exc:
        return _failed(index, _raw_source_id(raw), "validation_error", _first_message(exc))

    fields = normalize_patient(validated.model_dump())
    problem = _problem(fields, PATIENT_LIMITS, required=tuple(PATIENT_LIMITS))
    if problem is not None:
        return _failed(index, fields["source_id"] or None, "validation_error", problem)

    return _insert_patient(pool, index, fields)


def _insert_client(pool: ConnectionPool, index: int, fields: dict[str, Any]) -> RecordResult:
    digest = payload_hash(fields)
    try:
        return _write_client(pool, index, fields, digest)
    except errors.DataError as exc:
        # The record itself is the problem (a NUL byte, a value PostgreSQL cannot store).
        return _failed(index, fields["source_id"], "validation_error", _first_line(exc))
    except errors.IntegrityError as exc:
        return _failed(index, fields["source_id"], "database_error", _first_line(exc))


def _write_client(
    pool: ConnectionPool, index: int, fields: dict[str, Any], digest: str
) -> RecordResult:
    with pool.connection() as conn, conn.transaction():
        inserted = conn.execute(
            """
            INSERT INTO clients (source_id, first_name, last_name, email, payload_hash)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (source_id) DO NOTHING
            RETURNING id::text
            """,
            (
                fields["source_id"],
                fields["first_name"],
                fields["last_name"],
                fields["email"],
                digest,
            ),
        ).fetchone()
        if inserted is not None:
            return RecordResult(index, fields["source_id"], "created", inserted[0])

        existing = _existing(
            conn,
            "SELECT id::text, payload_hash FROM clients WHERE source_id = %s",
            fields["source_id"],
        )

    if existing is None:
        # The row was deleted between the insert attempt and the read: try once more.
        return _insert_client(pool, index, fields)

    return _settle(index, fields["source_id"], existing, digest)


def _insert_patient(pool: ConnectionPool, index: int, fields: dict[str, Any]) -> RecordResult:
    digest = payload_hash(fields)
    try:
        return _write_patient(pool, index, fields, digest)
    except errors.ForeignKeyViolation:
        # The owner disappeared between the resolution and the insert.
        return _failed(
            index,
            fields["source_id"],
            "owner_not_found",
            f"owner_source_id {fields['owner_source_id']} does not exist",
        )
    except errors.DataError as exc:
        return _failed(index, fields["source_id"], "validation_error", _first_line(exc))
    except errors.IntegrityError as exc:
        return _failed(index, fields["source_id"], "database_error", _first_line(exc))


def _write_patient(
    pool: ConnectionPool, index: int, fields: dict[str, Any], digest: str
) -> RecordResult:
    """Resolve the owner inside the insert, so an existing patient is settled first.

    With the owner resolved up front, a resent patient whose new ``owner_source_id`` does not
    exist answered ``owner_not_found`` even though the record was already there — the opposite of
    what the conflict policy promises. An `INSERT ... SELECT` from ``clients`` returns no row when
    the owner is missing, and no row either when the patient exists, so the follow-up read tells
    the two cases apart in the right order.
    """
    with pool.connection() as conn, conn.transaction():
        inserted = conn.execute(
            """
            INSERT INTO patients
                (source_id, name, species, date_of_birth, owner_id, owner_source_id, payload_hash)
            SELECT %s, %s, %s, %s, c.id, %s, %s
            FROM clients c
            WHERE c.source_id = %s
            ON CONFLICT (source_id) DO NOTHING
            RETURNING id::text
            """,
            (
                fields["source_id"],
                fields["name"],
                fields["species"],
                fields["date_of_birth"],
                fields["owner_source_id"],
                digest,
                fields["owner_source_id"],
            ),
        ).fetchone()
        if inserted is not None:
            return RecordResult(index, fields["source_id"], "created", inserted[0])

        existing = _existing(
            conn,
            "SELECT id::text, payload_hash FROM patients WHERE source_id = %s",
            fields["source_id"],
        )

    if existing is not None:
        return _settle(index, fields["source_id"], existing, digest)

    return _failed(
        index,
        fields["source_id"],
        "owner_not_found",
        f"owner_source_id {fields['owner_source_id']} does not exist",
    )


def _existing(conn: Connection, query: str, source_id: str) -> tuple[str, str] | None:
    """The committed row for a record that was already there: id and stored hash."""
    row = conn.execute(query, (source_id,)).fetchone()
    return (row[0], row[1]) if row is not None else None


def _settle(index: int, source_id: str, existing: tuple[str, str], digest: str) -> RecordResult:
    """Decide between ``already_existed`` and ``conflict`` for a record that was already there."""
    existing_id, existing_digest = existing
    if existing_digest == digest:
        return RecordResult(index, source_id, "already_existed", existing_id)
    return RecordResult(
        index,
        source_id,
        "conflict",
        existing_id,
        {"code": "payload_mismatch", "message": "source_id already exists with different data"},
    )


def _problem(
    fields: dict[str, Any], limits: dict[str, int], required: tuple[str, ...]
) -> str | None:
    """Return a message for the first invalid field, or None. Runs after normalisation."""
    for name, limit in limits.items():
        value = fields.get(name)
        if value is None:
            if name in required:
                return f"{name} is required"
            continue
        if name in required and not value:
            return f"{name} is required"
        if len(value) > limit:
            return f"{name} is longer than {limit} characters"
        if any(unicodedata.category(character) == "Cc" for character in value):
            # Rejected, never stripped: silently changing a value contradicts ADR 0002.
            return f"{name} contains a control character"
    return None


def _failed(index: int, source_id: str | None, code: str, message: str) -> RecordResult:
    return RecordResult(index, source_id, "failed", None, {"code": code, "message": message})


def _first_line(exc: Exception) -> str:
    return str(exc).strip().splitlines()[0][:200] if str(exc).strip() else type(exc).__name__


def _raw_source_id(raw: Any) -> str | None:
    if isinstance(raw, dict):
        value = raw.get("source_id")
        if isinstance(value, str):
            return value
    return None


def _first_message(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first["loc"])
    return f"{location}: {first['msg']}" if location else str(first["msg"])
