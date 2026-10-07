from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import CertificateStatus, JobStatus


# ---------- Requests ----------


class CertificateDetails(BaseModel):
    """Information shared by every certificate in the job."""

    model_config = ConfigDict(str_strip_whitespace=True)

    course_name: str = Field(min_length=1, max_length=200, examples=["Advanced Python Workshop"])
    issued_by: str = Field(min_length=1, max_length=120, examples=["AEREO Academy"])
    issue_date: date | None = Field(
        default=None, description="Defaults to today's date when omitted."
    )


class Recipient(BaseModel):
    """Strict schema applied to each recipient individually (see app/validation.py)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    name: str = Field(min_length=1, max_length=100)
    email: EmailStr


class JobCreate(BaseModel):
    certificate: CertificateDetails
    # Deliberately loose: each item is validated on its own so one bad recipient
    # does not cause the whole batch to be rejected.
    recipients: list[Any] = Field(
        min_length=1,
        examples=[[{"name": "Asha Rao", "email": "asha@example.com"}]],
    )


# ---------- Responses ----------


class Progress(BaseModel):
    total: int
    pending: int
    generated: int
    failed: int
    invalid: int
    processed: int = Field(description="generated + failed + invalid")
    percent_complete: float


class InvalidRecipient(BaseModel):
    index: int = Field(description="Position of the recipient in the submitted list (0-based).")
    errors: list[str]


class JobRead(BaseModel):
    id: str
    status: JobStatus
    certificate: CertificateDetails
    progress: Progress
    error: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    links: dict[str, str]


class JobCreated(JobRead):
    invalid_recipients: list[InvalidRecipient]


class CertificateRead(BaseModel):
    id: str
    job_id: str
    index: int
    recipient_name: str | None
    recipient_email: str | None
    status: CertificateStatus
    error: str | None
    generated_at: datetime | None
    download_url: str | None


class CertificatePage(BaseModel):
    items: list[CertificateRead]
    total: int
    limit: int
    offset: int
