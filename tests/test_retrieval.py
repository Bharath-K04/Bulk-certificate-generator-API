import zipfile
from io import BytesIO

from tests.conftest import make_payload
from tests.test_failure_handling import failing_for

JOBS_URL = "/api/v1/jobs"


def test_download_single_certificate(client):
    job = client.post(JOBS_URL, json=make_payload()).json()
    item = client.get(job["links"]["certificates"]).json()["items"][0]

    response = client.get(item["download_url"])

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "0001_Asha_Rao.pdf" in response.headers["content-disposition"]
    assert response.content.startswith(b"%PDF")


def test_get_single_certificate_metadata(client):
    job = client.post(JOBS_URL, json=make_payload()).json()
    item = client.get(job["links"]["certificates"]).json()["items"][0]

    response = client.get(f"/api/v1/certificates/{item['id']}")

    assert response.status_code == 200
    assert response.json() == item


def test_download_all_certificates_as_zip(client):
    job = client.post(JOBS_URL, json=make_payload()).json()

    response = client.get(job["links"]["download_zip"])

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["x-job-status"] == "completed"
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert names == ["0001_Asha_Rao.pdf", "0002_Vikram_Singh.pdf", "0003_Meera_Iyer.pdf"]
        assert all(archive.read(name).startswith(b"%PDF") for name in names)


def test_zip_only_contains_generated_certificates(make_client):
    with make_client(renderer=failing_for("Vikram Singh")) as client:
        job = client.post(JOBS_URL, json=make_payload()).json()
        response = client.get(job["links"]["download_zip"])

    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        assert archive.namelist() == ["0001_Asha_Rao.pdf", "0003_Meera_Iyer.pdf"]


def test_failed_certificate_cannot_be_downloaded(make_client):
    with make_client(renderer=failing_for("Vikram Singh")) as client:
        job = client.post(JOBS_URL, json=make_payload()).json()
        failed = client.get(f"{job['links']['certificates']}?status=failed").json()["items"][0]

        response = client.get(f"/api/v1/certificates/{failed['id']}/download")

    assert response.status_code == 409
    assert "failed" in response.json()["detail"]


def test_zip_not_available_before_anything_is_generated(paused_client):
    client, _ = paused_client
    job = client.post(JOBS_URL, json=make_payload()).json()

    response = client.get(job["links"]["download_zip"])

    assert response.status_code == 409


def test_unknown_certificate_returns_404(client):
    assert client.get("/api/v1/certificates/nope").status_code == 404
    assert client.get("/api/v1/certificates/nope/download").status_code == 404
    assert client.get(f"{JOBS_URL}/nope/download").status_code == 404
