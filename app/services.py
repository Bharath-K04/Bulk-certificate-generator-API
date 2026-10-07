"""Job creation and status queries. Kept free of FastAPI so it can be unit-tested directly."""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Certificate, CertificateStatus, Job, JobStatus, new_id
from app.schemas import CertificateDetails, Progress
from app.validation import RecipientResult, validate_recipients


class NoValidRecipientsError(Exception):
    def __init__(self, results: list[RecipientResult]) -> None:
        super().__init__("No valid recipients in request")
        self.results = results


def create_job(
    session: Session, details: CertificateDetails, raw_recipients: list
) -> tuple[Job, list[RecipientResult]]:
    """Validate recipients and persist the job plus one Certificate row per recipient.

    Valid recipients start as PENDING, invalid ones are stored as INVALID with their
    errors so the job report accounts for every submitted row. Returns the job and the
    list of invalid results. Raises NoValidRecipientsError if nothing can be generated.
    """
    results = validate_recipients(raw_recipients)
    invalid = [r for r in results if not r.is_valid]
    if len(invalid) == len(results):
        raise NoValidRecipientsError(results)

    job = Job(
        id=new_id(),
        status=JobStatus.PENDING,
        course_name=details.course_name,
        issued_by=details.issued_by,
        issue_date=details.issue_date or date.today(),
        total_recipients=len(results),
    )
    session.add(job)
    session.add_all(
        Certificate(
            job_id=job.id,
            row_index=r.index,
            recipient_name=r.raw_name,
            recipient_email=r.raw_email,
            status=CertificateStatus.PENDING if r.is_valid else CertificateStatus.INVALID,
            error=None if r.is_valid else "; ".join(r.errors),
        )
        for r in results
    )
    session.commit()
    return job, invalid


def count_by_status(session: Session, job_id: str) -> dict[CertificateStatus, int]:
    rows = session.execute(
        select(Certificate.status, func.count())
        .where(Certificate.job_id == job_id)
        .group_by(Certificate.status)
    ).all()
    counts = {status: 0 for status in CertificateStatus}
    counts.update({status: count for status, count in rows})
    return counts


def get_progress(session: Session, job: Job) -> Progress:
    # Computed from the certificate rows instead of stored counters, so it can never drift.
    counts = count_by_status(session, job.id)
    processed = (
        counts[CertificateStatus.GENERATED]
        + counts[CertificateStatus.FAILED]
        + counts[CertificateStatus.INVALID]
    )
    total = job.total_recipients
    return Progress(
        total=total,
        pending=counts[CertificateStatus.PENDING],
        generated=counts[CertificateStatus.GENERATED],
        failed=counts[CertificateStatus.FAILED],
        invalid=counts[CertificateStatus.INVALID],
        processed=processed,
        percent_complete=round(processed * 100 / total, 1) if total else 100.0,
    )


def reset_failed_certificates(session: Session, job: Job) -> int:
    """Move FAILED certificates back to PENDING so the processor picks them up again."""
    failed = session.scalars(
        select(Certificate).where(
            Certificate.job_id == job.id, Certificate.status == CertificateStatus.FAILED
        )
    ).all()
    for certificate in failed:
        certificate.status = CertificateStatus.PENDING
        certificate.error = None
    if failed:
        job.status = JobStatus.PENDING
        job.completed_at = None
        job.error = None
    session.commit()
    return len(failed)


def unfinished_job_ids(session: Session) -> list[str]:
    return list(
        session.scalars(
            select(Job.id)
            .where(Job.status.in_([JobStatus.PENDING, JobStatus.PROCESSING]))
            .order_by(Job.created_at)
        )
    )
