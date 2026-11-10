from datetime import date

from bulk_import.normalize import normalize_client, normalize_patient, payload_hash


def test_key_order_does_not_change_the_hash() -> None:
    first = {"source_id": "C-1", "first_name": "Ana", "last_name": "Ruiz", "email": None}
    second = {"email": None, "last_name": "Ruiz", "first_name": "Ana", "source_id": "C-1"}

    assert payload_hash(first) == payload_hash(second)


def test_absent_and_null_optional_fields_are_the_same_data() -> None:
    absent = normalize_client({"source_id": "C-1", "first_name": "Ana", "last_name": "Ruiz"})
    explicit_null = normalize_client(
        {"source_id": "C-1", "first_name": "Ana", "last_name": "Ruiz", "email": None}
    )
    empty_string = normalize_client(
        {"source_id": "C-1", "first_name": "Ana", "last_name": "Ruiz", "email": "   "}
    )

    assert absent == explicit_null == empty_string
    assert absent["email"] is None


def test_cosmetic_differences_normalise_to_the_same_hash() -> None:
    clean = normalize_client(
        {"source_id": "C-1", "first_name": "Ana Maria", "last_name": "Ruiz", "email": "ana@x.com"}
    )
    messy = normalize_client(
        {
            "source_id": " C-1 ",
            "first_name": "  Ana   Maria ",
            "last_name": "Ruiz\t",
            "email": "  ANA@X.com ",
        }
    )

    assert messy == clean
    assert payload_hash(messy) == payload_hash(clean)


def test_a_real_difference_gives_a_different_hash() -> None:
    before = normalize_client({"source_id": "C-1", "first_name": "Ana", "last_name": "Ruiz"})
    after = normalize_client({"source_id": "C-1", "first_name": "Ana", "last_name": "Ruiz Gil"})

    assert payload_hash(before) != payload_hash(after)


def test_source_id_keeps_its_case_because_it_is_the_identity() -> None:
    lower = normalize_client({"source_id": "c-1", "first_name": "Ana", "last_name": "Ruiz"})
    upper = normalize_client({"source_id": "C-1", "first_name": "Ana", "last_name": "Ruiz"})

    assert payload_hash(lower) != payload_hash(upper)


def test_patient_dates_are_hashed_as_iso_strings() -> None:
    with_date = normalize_patient(
        {
            "source_id": "P-1",
            "name": "Luna",
            "species": " Dog ",
            "date_of_birth": date(2020, 1, 2),
            "owner_source_id": "C-1",
        }
    )
    without_date = normalize_patient(
        {"source_id": "P-1", "name": "Luna", "species": "dog", "owner_source_id": "C-1"}
    )

    assert with_date["date_of_birth"] == "2020-01-02"
    assert with_date["species"] == "dog"
    assert without_date["date_of_birth"] is None
    assert payload_hash(with_date) != payload_hash(without_date)
