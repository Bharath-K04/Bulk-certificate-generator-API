"""How jobs get executed. The API only depends on the `Dispatcher` interface, so the
thread pool could be replaced by Celery/RQ without touching the endpoints."""

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

logger = logging.getLogger(__name__)

JobHandler = Callable[[str], None]


class Dispatcher(Protocol):
    def submit(self, job_id: str) -> None: ...

    def shutdown(self) -> None: ...


class ThreadPoolDispatcher:
    """Runs jobs on a small in-process thread pool; the HTTP request returns immediately."""

    def __init__(self, handler: JobHandler, max_workers: int) -> None:
        self._handler = handler
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="certgen-worker"
        )

    def submit(self, job_id: str) -> None:
        self._executor.submit(self._run, job_id)

    def _run(self, job_id: str) -> None:
        try:
            self._handler(job_id)
        except Exception:
            logger.exception("Unhandled error while processing job %s", job_id)

    def shutdown(self) -> None:
        # Queued jobs are dropped (they are still PENDING in the DB and resume on restart).
        self._executor.shutdown(wait=True, cancel_futures=True)


class InlineDispatcher:
    """Runs the job synchronously inside the request. Deterministic, used by tests."""

    def __init__(self, handler: JobHandler) -> None:
        self._handler = handler

    def submit(self, job_id: str) -> None:
        self._handler(job_id)

    def shutdown(self) -> None:
        pass
