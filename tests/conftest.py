from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.generator import Renderer
from app.main import create_app


class RecordingDispatcher:
    """Records submitted jobs without running them, so tests can inspect the PENDING state."""

    def __init__(self) -> None:
        self.submitted: list[str] = []

    def submit(self, job_id: str) -> None:
        self.submitted.append(job_id)

    def shutdown(self) -> None:
        pass


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        storage_dir=tmp_path / "storage",
        processing_mode="inline",  # deterministic: the job finishes inside the POST
        max_recipients_per_job=50,
    )


@pytest.fixture
def make_client(settings: Settings) -> Callable:
    @contextmanager
    def _make(renderer: Renderer | None = None) -> Iterator[TestClient]:
        with TestClient(create_app(settings, renderer=renderer)) as client:
            yield client

    return _make


@pytest.fixture
def client(make_client) -> Iterator[TestClient]:
    with make_client() as client:
        yield client


@pytest.fixture
def paused_client(client: TestClient) -> tuple[TestClient, RecordingDispatcher]:
    """Client whose jobs are accepted but not processed until the test runs them."""
    dispatcher = RecordingDispatcher()
    client.app.state.dispatcher = dispatcher
    return client, dispatcher


def make_payload(recipients: list | None = None, **certificate) -> dict:
    return {
        "certificate": {
            "course_name": "Advanced Python Workshop",
            "issued_by": "AEREO Academy",
            "issue_date": "2026-10-01",
            **certificate,
        },
        "recipients": recipients
        if recipients is not None
        else [
            {"name": "Asha Rao", "email": "asha@example.com"},
            {"name": "Vikram Singh", "email": "vikram@example.com"},
            {"name": "Meera Iyer", "email": "meera@example.com"},
        ],
    }
