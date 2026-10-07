from app.generator import render_certificate_pdf
from tests.conftest import RecordingDispatcher, make_payload

JOBS_URL = "/api/v1/jobs"


def failing_for(*names):
    """A renderer that raises for specific recipients and works normally for the rest."""

    def renderer(content, path):
        if content.recipient_name in names:
            raise RuntimeError(f"template engine crashed for {content.recipient_name}")
        render_certificate_pdf(content, path)

    return renderer


def test_one_failure_does_not_stop_the_other_certificates(make_client):
    with make_client(renderer=failing_for("Vikram Singh")) as client:
        job = client.post(JOBS_URL, json=make_payload()).json()
        items = client.get(job["links"]["certificates"]).json()["items"]

    assert job["status"] == "completed_with_errors"
    assert job["progress"]["generated"] == 2
    assert job["progress"]["failed"] == 1

    by_name = {item["recipient_name"]: item for item in items}
    failed = by_name["Vikram Singh"]
    assert failed["status"] == "failed"
    assert failed["error"] == "RuntimeError: template engine crashed for Vikram Singh"
    assert failed["download_url"] is None
    # Recipients after the failing one were still generated.
    assert by_name["Meera Iyer"]["status"] == "generated"
    assert by_name["Asha Rao"]["status"] == "generated"


def test_job_fails_when_every_certificate_fails(make_client):
    names = ("Asha Rao", "Vikram Singh", "Meera Iyer")
    with make_client(renderer=failing_for(*names)) as client:
        job = client.post(JOBS_URL, json=make_payload()).json()

    assert job["status"] == "failed"
    assert job["progress"]["failed"] == 3


def test_failed_certificates_can_be_retried(make_client):
    broken = {"Vikram Singh"}

    def flaky(content, path):
        if content.recipient_name in broken:
            raise OSError("disk full")
        render_certificate_pdf(content, path)

    with make_client(renderer=flaky) as client:
        job = client.post(JOBS_URL, json=make_payload()).json()
        assert job["progress"]["failed"] == 1

        broken.clear()  # the transient problem goes away
        retried = client.post(f"{JOBS_URL}/{job['id']}/retry")
        assert retried.status_code == 202
        final = client.get(f"{JOBS_URL}/{job['id']}").json()

        # Nothing left to retry now.
        assert client.post(f"{JOBS_URL}/{job['id']}/retry").status_code == 409

    assert final["status"] == "completed"
    assert final["progress"]["generated"] == 3
    assert final["progress"]["failed"] == 0


def test_retry_rejected_while_job_is_running(paused_client):
    client, _ = paused_client
    job = client.post(JOBS_URL, json=make_payload()).json()

    assert client.post(f"{JOBS_URL}/{job['id']}/retry").status_code == 409


def test_unfinished_jobs_resume_after_restart(make_client):
    # First "server run": job is accepted but the process dies before it is processed.
    with make_client() as client:
        client.app.state.dispatcher = RecordingDispatcher()  # never runs the job
        job_id = client.post(JOBS_URL, json=make_payload()).json()["id"]
        assert client.get(f"{JOBS_URL}/{job_id}").json()["status"] == "pending"

    # Second run against the same database: startup recovery finishes the job.
    with make_client() as client:
        job = client.get(f"{JOBS_URL}/{job_id}").json()

    assert job["status"] == "completed"
    assert job["progress"]["generated"] == 3
