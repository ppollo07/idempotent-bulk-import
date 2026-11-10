"""Normalisation of incoming business fields.

Normalisation happens once, on ingress, before a record is hashed or written. What is stored
is therefore what was compared: there is a single definition of "the same data", and a retry
that differs only in cosmetic whitespace or email case is not a conflict.

What is *not* normalised: accents, punctuation, synonyms, near-duplicate detection. Anything
that survives normalisation is a real difference between two versions of the same record.
"""

import json
import unicodedata
from datetime import date
from hashlib import sha256
from typing import Any


def _text(value: str) -> str:
    """Unicode NFC, trimmed, with internal whitespace collapsed."""
    return " ".join(unicodedata.normalize("NFC", value).split())


def _lower_text(value: str) -> str:
    return _text(value).lower()


def _optional_text(value: str | None) -> str | None:
    """An absent field, an explicit null and an empty string are the same data."""
    if value is None:
        return None
    return _text(value) or None


def _optional_lower_text(value: str | None) -> str | None:
    normalized = _optional_text(value)
    return normalized.lower() if normalized is not None else None


def normalize_client(record: dict[str, Any]) -> dict[str, Any]:
    """Return the business fields of a client in their stored, hashed form."""
    return {
        "source_id": _text(record["source_id"]),
        "first_name": _text(record["first_name"]),
        "last_name": _text(record["last_name"]),
        "email": _optional_lower_text(record.get("email")),
    }


def normalize_patient(record: dict[str, Any]) -> dict[str, Any]:
    """Return the business fields of a patient in their stored, hashed form."""
    date_of_birth = record.get("date_of_birth")
    return {
        "source_id": _text(record["source_id"]),
        "name": _text(record["name"]),
        "species": _lower_text(record["species"]),
        "date_of_birth": date_of_birth.isoformat() if isinstance(date_of_birth, date) else None,
        "owner_source_id": _text(record["owner_source_id"]),
    }


def payload_hash(fields: dict[str, Any]) -> str:
    """SHA-256 of the canonical JSON of the normalised business fields."""
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(canonical.encode("utf-8")).hexdigest()
