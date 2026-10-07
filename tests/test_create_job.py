from datetime import date

from tests.conftest import make_payload

JOBS_URL = "/api/v1/jobs"


def test_create_job_returns_202_with_job_details(client):
    response = client.post(JOBS_URL, json=make_payload())

    assert response.status_code == 202
    body = response.json()
    assert body["id"]
    assert body["certificate"] == {
        "course_name": "Advanced Python Workshop",
        "issued_by": "AEREO Academy",
        "issue_date": "2026-10-01",
    }
    assert body["progress"]["total"] == 3
    assert body["invalid_recipients"] == []
    assert body["links"]["self"] == f"{JOBS_URL}/{body['id']}"


def test_create_job_generates_every_certificate(client):
    body = client.post(JOBS_URL, json=make_payload()).json()

    assert body["status"] == "completed"
    assert body["progress"]["generated"] == 3
    assert body["progress"]["percent_complete"] == 100.0


def test_create_job_is_dispatched_once(paused_client):
    client, dispatcher = paused_client

    body = client.post(JOBS_URL, json=make_payload()).json()

    assert dispatcher.submitted == [body["id"]]
    assert body["status"] == "pending"


def test_issue_date_defaults_to_today(client):
    payload = make_payload()
    del payload["certificate"]["issue_date"]

    body = client.post(JOBS_URL, json=payload).json()

    assert body["certificate"]["issue_date"] == date.today().isoformat()


def test_whitespace_is_trimmed(client):
    payload = make_payload(
        recipients=[{"name": "  Asha Rao  ", "email": " asha@example.com "}],
        course_name="  Data Engineering  ",
    )

    body = client.post(JOBS_URL, json=payload).json()
    items = client.get(body["links"]["certificates"]).json()["items"]

    assert body["certificate"]["course_name"] == "Data Engineering"
    assert items[0]["recipient_name"] == "Asha Rao"
    assert items[0]["recipient_email"] == "asha@example.com"
