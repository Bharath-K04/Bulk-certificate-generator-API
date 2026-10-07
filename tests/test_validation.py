import pytest

from app.validation import validate_recipients
from tests.conftest import make_payload

JOBS_URL = "/api/v1/jobs"


# ---------- Request-level validation: the whole request is rejected ----------


@pytest.mark.parametrize(
    "payload",
    [
        {"recipients": [{"name": "A", "email": "a@example.com"}]},  # no certificate block
        {"certificate": {"course_name": "X", "issued_by": "Y"}},  # no recipients
        make_payload(recipients=[]),  # empty recipients
        {"certificate": {"course_name": "X", "issued_by": "Y"}, "recipients": "nope"},
        make_payload(course_name="   "),  # blank course name
        make_payload(issued_by=""),
        make_payload(issue_date="not-a-date"),
        make_payload(course_name="x" * 201),
    ],
)
def test_malformed_requests_are_rejected(client, payload):
    response = client.post(JOBS_URL, json=payload)
    assert response.status_code == 422


def test_too_many_recipients_is_rejected(client):
    recipients = [{"name": f"User {i}", "email": f"user{i}@example.com"} for i in range(51)]

    response = client.post(JOBS_URL, json=make_payload(recipients=recipients))

    assert response.status_code == 422
    assert "at most 50" in response.json()["detail"]


def test_all_invalid_recipients_rejects_request_and_creates_no_job(client):
    recipients = [{"name": "", "email": "bad"}, {"email": "x@example.com"}]

    response = client.post(JOBS_URL, json=make_payload(recipients=recipients))

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert [r["index"] for r in detail["invalid_recipients"]] == [0, 1]
    with client.app.state.database.session_factory() as session:
        from app.models import Job

        assert session.query(Job).count() == 0


# ---------- Recipient-level validation: bad rows are skipped, the rest proceed ----------


def test_invalid_recipients_are_reported_and_valid_ones_generated(client):
    recipients = [
        {"name": "Asha Rao", "email": "asha@example.com"},  # 0 valid
        {"name": "No Email"},  # 1 missing email
        {"name": "Bad Email", "email": "not-an-email"},  # 2 malformed email
        {"name": "   ", "email": "blank@example.com"},  # 3 blank name
        "just a string",  # 4 not an object
        {"name": "Asha Again", "email": "ASHA@example.com"},  # 5 duplicate (case-insensitive)
        {"name": "Vikram Singh", "email": "vikram@example.com"},  # 6 valid
    ]

    response = client.post(JOBS_URL, json=make_payload(recipients=recipients))

    assert response.status_code == 202
    body = response.json()
    invalid = {r["index"]: r["errors"] for r in body["invalid_recipients"]}
    assert set(invalid) == {1, 2, 3, 4, 5}
    assert "email" in invalid[1][0]
    assert "email" in invalid[2][0]
    assert "name" in invalid[3][0]
    assert "JSON object" in invalid[4][0]
    assert "duplicate of recipient at index 0" in invalid[5][0]

    assert body["status"] == "completed_with_errors"
    assert body["progress"]["generated"] == 2
    assert body["progress"]["invalid"] == 5
    assert body["progress"]["total"] == 7


def test_invalid_recipients_are_stored_with_their_errors(client):
    recipients = [
        {"name": "Asha Rao", "email": "asha@example.com"},
        {"name": "Bad Email", "email": "nope"},
    ]
    body = client.post(JOBS_URL, json=make_payload(recipients=recipients)).json()

    invalid = client.get(f"{body['links']['certificates']}?status=invalid").json()["items"]

    assert len(invalid) == 1
    assert invalid[0]["index"] == 1
    assert invalid[0]["recipient_name"] == "Bad Email"
    assert invalid[0]["download_url"] is None
    assert "email" in invalid[0]["error"]


def test_validate_recipients_unit():
    results = validate_recipients(
        [
            {"name": "Asha", "email": "asha@example.com", "unknown_field": 1},
            {"name": "x" * 101, "email": "long@example.com"},
            None,
        ]
    )

    assert [r.is_valid for r in results] == [True, False, False]
    assert results[0].recipient.name == "Asha"
    assert "name" in results[1].errors[0]
    assert results[2].errors == ["recipient must be a JSON object"]
