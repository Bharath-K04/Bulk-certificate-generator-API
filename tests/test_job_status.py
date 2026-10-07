from app.generator import render_certificate_pdf
from app.processing import JobProcessor
from tests.conftest import make_payload

JOBS_URL = "/api/v1/jobs"


def test_status_of_pending_job_then_completed(paused_client):
    client, dispatcher = paused_client
    job_id = client.post(JOBS_URL, json=make_payload()).json()["id"]

    pending = client.get(f"{JOBS_URL}/{job_id}").json()
    assert pending["status"] == "pending"
    assert pending["progress"] == {
        "total": 3,
        "pending": 3,
        "generated": 0,
        "failed": 0,
        "invalid": 0,
        "processed": 0,
        "percent_complete": 0.0,
    }
    assert pending["started_at"] is None

    # Now let the worker run the job.
    client.app.state.processor.process(dispatcher.submitted[0])

    done = client.get(f"{JOBS_URL}/{job_id}").json()
    assert done["status"] == "completed"
    assert done["progress"]["generated"] == 3
    assert done["progress"]["percent_complete"] == 100.0
    assert done["started_at"] is not None
    assert done["completed_at"] is not None


def test_progress_is_reported_mid_job(paused_client, settings):
    """Status requests made while a job is running see partial progress."""
    client, _ = paused_client
    job_id = client.post(JOBS_URL, json=make_payload()).json()["id"]
    snapshots = []

    def observing_renderer(content, path):
        # Called right before each certificate is generated.
        snapshots.append(client.get(f"{JOBS_URL}/{job_id}").json())
        render_certificate_pdf(content, path)

    processor = JobProcessor(
        client.app.state.database.session_factory, settings.storage_dir, observing_renderer
    )
    processor.process(job_id)

    assert [s["status"] for s in snapshots] == ["processing"] * 3
    assert [s["progress"]["generated"] for s in snapshots] == [0, 1, 2]
    assert [s["progress"]["percent_complete"] for s in snapshots] == [0.0, 33.3, 66.7]


def test_unknown_job_returns_404(client):
    assert client.get(f"{JOBS_URL}/does-not-exist").status_code == 404
    assert client.get(f"{JOBS_URL}/does-not-exist/certificates").status_code == 404


def test_certificate_list_is_paginated_and_ordered(client):
    recipients = [{"name": f"User {i}", "email": f"user{i}@example.com"} for i in range(7)]
    job = client.post(JOBS_URL, json=make_payload(recipients=recipients)).json()

    page = client.get(f"{JOBS_URL}/{job['id']}/certificates?limit=3&offset=3").json()

    assert page["total"] == 7
    assert page["limit"] == 3
    assert page["offset"] == 3
    assert [item["index"] for item in page["items"]] == [3, 4, 5]
    assert [item["recipient_name"] for item in page["items"]] == ["User 3", "User 4", "User 5"]


def test_certificate_list_status_filter(client):
    recipients = [
        {"name": "Ok", "email": "ok@example.com"},
        {"name": "Bad", "email": "bad"},
    ]
    job = client.post(JOBS_URL, json=make_payload(recipients=recipients)).json()

    generated = client.get(f"{JOBS_URL}/{job['id']}/certificates?status=generated").json()
    invalid = client.get(f"{JOBS_URL}/{job['id']}/certificates?status=invalid").json()

    assert [i["recipient_name"] for i in generated["items"]] == ["Ok"]
    assert [i["recipient_name"] for i in invalid["items"]] == ["Bad"]
    bad_filter = client.get(f"{JOBS_URL}/{job['id']}/certificates?status=bogus")
    assert bad_filter.status_code == 422
