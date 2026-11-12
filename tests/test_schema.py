"""The schema itself: the constraints and indexes the design depends on."""

import psycopg


def test_the_owner_foreign_key_is_indexed(clean_db) -> None:
    """Postgres indexes the parent side of a foreign key, never the child.

    Without this index, deleting a client and counting patients per owner both read the whole
    table — and the per-owner report in the README is a join on `owner_id`.
    """
    rows = clean_db.execute(
        "SELECT indexdef FROM pg_indexes WHERE tablename = 'patients'"
    ).fetchall()

    assert any("owner_id" in row[0] for row in rows), rows


def test_source_id_is_unique_on_both_tables(clean_db) -> None:
    rows = clean_db.execute(
        "SELECT tablename, indexdef FROM pg_indexes WHERE indexdef LIKE '%UNIQUE%'"
    ).fetchall()

    unique = {table for table, definition in rows if "source_id" in definition}
    assert {"clients", "patients"} <= unique, rows
