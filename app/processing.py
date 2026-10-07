"""Generates the certificates of a job. Runs on a worker thread (or inline in tests)."""

import logging
import threading
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.generator import CertificateContent, Renderer, render_certificate_pdf
from app.models import (
    TERMINAL_JOB_STATUSES,
    Certificate,
    CertificateStatus,
    Job,
    JobStatus,
    utcnow,
)
from app.services import count_by_status

logger = logging.getLogger(__name__)

MAX_ERROR_LENGTH = 500


class JobProcessor:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        storage_dir: Path,
        renderer: Renderer = render_certificate_pdf,
    ) -> None:
        self._session_factory = session_factory
        self._storage_dir = storage_dir
        self._renderer = renderer
        self._stop = threading.Event()

    def request_stop(self) -> None:
        """Ask running jobs to stop after their current certificate (used on shutdown).

        Stopped jobs keep their PENDING certificates and are resumed on next startup.
        """
        self._stop.set()

    def process(self, job_id: str) -> None:
        try:
            self._process(job_id)
        except Exception as exc:  # anything unexpected outside a single certificate
            logger.exception("Job %s crashed", job_id)
            self._mark_job_failed(job_id, f"Unexpected error: {exc}")

    def _process(self, job_id: str) -> None:
        with self._session_factory() as session:
            job = session.get(Job, job_id)
            if job is None:
                logger.warning("Job %s not found", job_id)
                return
            if job.status in TERMINAL_JOB_STATUSES:
                return

            job.status = JobStatus.PROCESSING
            job.started_at = job.started_at or utcnow()
            session.commit()

            pending_ids = session.scalars(
                select(Certificate.id)
                .where(
                    Certificate.job_id == job_id,
                    Certificate.status == CertificateStatus.PENDING,
                )
                .order_by(Certificate.row_index)
            ).all()

        logger.info("Job %s: generating %d certificate(s)", job_id, len(pending_ids))
        for certificate_id in pending_ids:
            if self._stop.is_set():
                logger.info("Job %s interrupted; it will resume on next startup", job_id)
                return
            self._generate_one(certificate_id)

        self._finalize(job_id)

    def _generate_one(self, certificate_id: str) -> None:
        """Generate one certificate in its own transaction.

        Errors are caught *per certificate*, so one failure never stops the rest of the
        job, and committing after each one makes progress visible to status requests.
        """
        with self._session_factory() as session:
            certificate = session.get(Certificate, certificate_id)
            if certificate is None or certificate.status != CertificateStatus.PENDING:
                return
            job = certificate.job
            relative_path = Path(job.id) / f"{certificate.id}.pdf"
            content = CertificateContent(
                certificate_id=certificate.id,
                recipient_name=certificate.recipient_name or "",
                course_name=job.course_name,
                issued_by=job.issued_by,
                issue_date=job.issue_date,
            )

            try:
                self._renderer(content, self._storage_dir / relative_path)
            except Exception as exc:
                logger.warning("Certificate %s failed", certificate.id, exc_info=True)
                certificate.status = CertificateStatus.FAILED
                certificate.error = f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LENGTH]
            else:
                certificate.status = CertificateStatus.GENERATED
                certificate.file_path = relative_path.as_posix()
                certificate.generated_at = utcnow()
            session.commit()

    def _finalize(self, job_id: str) -> None:
        with self._session_factory() as session:
            job = session.get(Job, job_id)
            counts = count_by_status(session, job_id)
            if counts[CertificateStatus.PENDING]:
                return  # still work to do (should not normally happen)

            generated = counts[CertificateStatus.GENERATED]
            problems = counts[CertificateStatus.FAILED] + counts[CertificateStatus.INVALID]
            if problems == 0:
                job.status = JobStatus.COMPLETED
            elif generated > 0:
                job.status = JobStatus.COMPLETED_WITH_ERRORS
            else:
                job.status = JobStatus.FAILED
            job.completed_at = utcnow()
            session.commit()
            logger.info("Job %s finished: %s", job_id, job.status)

    def _mark_job_failed(self, job_id: str, message: str) -> None:
        try:
            with self._session_factory() as session:
                job = session.get(Job, job_id)
                if job is not None:
                    job.status = JobStatus.FAILED
                    job.error = message[:MAX_ERROR_LENGTH]
                    job.completed_at = utcnow()
                    session.commit()
        except Exception:
            logger.exception("Could not mark job %s as failed", job_id)
