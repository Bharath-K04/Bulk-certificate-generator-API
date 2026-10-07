import uuid
from datetime import date, datetime, timezone
from enum import StrEnum

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class JobStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"  # every recipient got a certificate
    COMPLETED_WITH_ERRORS = "completed_with_errors"  # some generated, some failed/invalid
    FAILED = "failed"  # nothing could be generated


TERMINAL_JOB_STATUSES = {JobStatus.COMPLETED, JobStatus.COMPLETED_WITH_ERRORS, JobStatus.FAILED}


class CertificateStatus(StrEnum):
    PENDING = "pending"  # valid, waiting to be generated
    GENERATED = "generated"  # PDF exists on disk
    FAILED = "failed"  # valid data, but generation raised an error (retryable)
    INVALID = "invalid"  # rejected by validation, never generated


def _enum_column(enum_cls: type[StrEnum]) -> Enum:
    # Store the enum *values* ("pending") as plain strings rather than a native DB enum,
    # so adding a new status never needs an ALTER TYPE migration.
    return Enum(
        enum_cls,
        native_enum=False,
        length=32,
        values_callable=lambda members: [m.value for m in members],
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    status: Mapped[JobStatus] = mapped_column(
        _enum_column(JobStatus), default=JobStatus.PENDING, index=True
    )
    course_name: Mapped[str] = mapped_column(String(200))
    issued_by: Mapped[str] = mapped_column(String(120))
    issue_date: Mapped[date] = mapped_column(Date)
    total_recipients: Mapped[int] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text, default=None)  # job-level error only
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    certificates: Mapped[list["Certificate"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", passive_deletes=True
    )


class Certificate(Base):
    """One row per submitted recipient (valid or not), so every input row is accounted for."""

    __tablename__ = "certificates"
    __table_args__ = (
        UniqueConstraint("job_id", "row_index", name="uq_certificates_job_row"),
        Index("ix_certificates_job_status", "job_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    # Position of the recipient in the submitted list, so clients can map errors back.
    row_index: Mapped[int] = mapped_column(Integer)
    recipient_name: Mapped[str | None] = mapped_column(String(200), default=None)
    recipient_email: Mapped[str | None] = mapped_column(String(320), default=None)
    status: Mapped[CertificateStatus] = mapped_column(
        _enum_column(CertificateStatus), default=CertificateStatus.PENDING
    )
    error: Mapped[str | None] = mapped_column(Text, default=None)
    # Path relative to the storage directory, e.g. "<job_id>/<certificate_id>.pdf".
    file_path: Mapped[str | None] = mapped_column(String(500), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    job: Mapped[Job] = relationship(back_populates="certificates")
