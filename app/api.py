import os
import re
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.config import Settings
from app.models import TERMINAL_JOB_STATUSES, Certificate, CertificateStatus, Job
from app.schemas import (
    CertificateDetails,
    CertificatePage,
    CertificateRead,
    InvalidRecipient,
    JobCreate,
    JobCreated,
    JobRead,
)
from app.services import (
    NoValidRecipientsError,
    create_job,
    get_progress,
    reset_failed_certificates,
)
from app.worker import Dispatcher

API_PREFIX = "/api/v1"
router = APIRouter(prefix=API_PREFIX)


# ---------- Dependencies ----------


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.database.session_factory() as session:
        yield session


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_dispatcher(request: Request) -> Dispatcher:
    return request.app.state.dispatcher


SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
DispatcherDep = Annotated[Dispatcher, Depends(get_dispatcher)]


# ---------- Jobs ----------


@router.post(
    "/jobs",
    response_model=JobCreated,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a bulk certificate generation job",
)
def submit_job(
    payload: JobCreate, session: SessionDep, settings: SettingsDep, dispatcher: DispatcherDep
) -> JobCreated:
    if len(payload.recipients) > settings.max_recipients_per_job:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"A job may contain at most {settings.max_recipients_per_job} recipients "
            f"(got {len(payload.recipients)}). Split the list into several jobs.",
        )

    try:
        job, invalid = create_job(session, payload.certificate, payload.recipients)
    except NoValidRecipientsError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "message": "None of the recipients are valid; no job was created.",
                "invalid_recipients": [
                    {"index": r.index, "errors": r.errors} for r in exc.results
                ],
            },
        ) from exc

    # Dispatch only after commit, so the worker is guaranteed to see the rows.
    dispatcher.submit(job.id)

    session.refresh(job)
    return JobCreated(
        **_job_read(session, job).model_dump(),
        invalid_recipients=[InvalidRecipient(index=r.index, errors=r.errors) for r in invalid],
    )


@router.get("/jobs/{job_id}", response_model=JobRead, summary="Get job status and progress")
def get_job(job_id: str, session: SessionDep) -> JobRead:
    return _job_read(session, _get_job_or_404(session, job_id))


@router.get(
    "/jobs/{job_id}/certificates",
    response_model=CertificatePage,
    summary="List the per-recipient results of a job (paginated, filterable by status)",
)
def list_job_certificates(
    job_id: str,
    session: SessionDep,
    status_filter: Annotated[CertificateStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CertificatePage:
    _get_job_or_404(session, job_id)
    conditions = [Certificate.job_id == job_id]
    if status_filter is not None:
        conditions.append(Certificate.status == status_filter)

    total = session.scalar(select(func.count()).select_from(Certificate).where(*conditions))
    certificates = session.scalars(
        select(Certificate)
        .where(*conditions)
        .order_by(Certificate.row_index)
        .limit(limit)
        .offset(offset)
    ).all()
    return CertificatePage(
        items=[_certificate_read(c) for c in certificates],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/jobs/{job_id}/download",
    response_class=FileResponse,
    summary="Download all generated certificates of a job as a ZIP",
    responses={200: {"content": {"application/zip": {}}}},
)
def download_job_zip(job_id: str, session: SessionDep, settings: SettingsDep) -> FileResponse:
    job = _get_job_or_404(session, job_id)
    generated = session.scalars(
        select(Certificate)
        .where(Certificate.job_id == job.id, Certificate.status == CertificateStatus.GENERATED)
        .order_by(Certificate.row_index)
    ).all()
    if not generated:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"No generated certificates available yet (job status: {job.status}).",
        )

    # Build the archive in a temp file (not in memory) and delete it after sending.
    fd, zip_path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for certificate in generated:
                pdf_path = _resolve_file(settings, certificate)
                if pdf_path is not None:
                    archive.write(pdf_path, arcname=_download_name(certificate))
    except Exception:
        os.remove(zip_path)
        raise

    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"certificates_{job.id}.zip",
        headers={"X-Job-Status": job.status.value},
        background=BackgroundTask(os.remove, zip_path),
    )


@router.post(
    "/jobs/{job_id}/retry",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-run generation for the certificates that failed",
)
def retry_failed(job_id: str, session: SessionDep, dispatcher: DispatcherDep) -> JobRead:
    job = _get_job_or_404(session, job_id)
    if job.status not in TERMINAL_JOB_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "Job is still being processed.")
    if reset_failed_certificates(session, job) == 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "Job has no failed certificates to retry.")
    dispatcher.submit(job.id)
    session.refresh(job)
    return _job_read(session, job)


# ---------- Certificates ----------


@router.get(
    "/certificates/{certificate_id}",
    response_model=CertificateRead,
    summary="Get a single certificate's status",
)
def get_certificate(certificate_id: str, session: SessionDep) -> CertificateRead:
    return _certificate_read(_get_certificate_or_404(session, certificate_id))


@router.get(
    "/certificates/{certificate_id}/download",
    response_class=FileResponse,
    summary="Download a single certificate PDF",
    responses={200: {"content": {"application/pdf": {}}}},
)
def download_certificate(
    certificate_id: str, session: SessionDep, settings: SettingsDep
) -> FileResponse:
    certificate = _get_certificate_or_404(session, certificate_id)
    if certificate.status != CertificateStatus.GENERATED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Certificate is not available (status: {certificate.status}).",
        )
    pdf_path = _resolve_file(settings, certificate)
    if pdf_path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Certificate file is missing.")
    return FileResponse(
        pdf_path, media_type="application/pdf", filename=_download_name(certificate)
    )


# ---------- Helpers ----------


def _get_job_or_404(session: Session, job_id: str) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found.")
    return job


def _get_certificate_or_404(session: Session, certificate_id: str) -> Certificate:
    certificate = session.get(Certificate, certificate_id)
    if certificate is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Certificate not found.")
    return certificate


def _job_read(session: Session, job: Job) -> JobRead:
    base = f"{API_PREFIX}/jobs/{job.id}"
    return JobRead(
        id=job.id,
        status=job.status,
        certificate=CertificateDetails(
            course_name=job.course_name, issued_by=job.issued_by, issue_date=job.issue_date
        ),
        progress=get_progress(session, job),
        error=job.error,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        links={
            "self": base,
            "certificates": f"{base}/certificates",
            "failed_certificates": f"{base}/certificates?status=failed",
            "download_zip": f"{base}/download",
        },
    )


def _certificate_read(certificate: Certificate) -> CertificateRead:
    download_url = None
    if certificate.status == CertificateStatus.GENERATED:
        download_url = f"{API_PREFIX}/certificates/{certificate.id}/download"
    return CertificateRead(
        id=certificate.id,
        job_id=certificate.job_id,
        index=certificate.row_index,
        recipient_name=certificate.recipient_name,
        recipient_email=certificate.recipient_email,
        status=certificate.status,
        error=certificate.error,
        generated_at=certificate.generated_at,
        download_url=download_url,
    )


def _resolve_file(settings: Settings, certificate: Certificate) -> Path | None:
    if not certificate.file_path:
        return None
    storage_root = settings.storage_dir.resolve()
    path = (storage_root / certificate.file_path).resolve()
    # Defence in depth: never serve anything outside the storage directory.
    if storage_root not in path.parents or not path.is_file():
        return None
    return path


def _download_name(certificate: Certificate) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", certificate.recipient_name or "").strip("_")[:50]
    return f"{certificate.row_index + 1:04d}_{slug or 'recipient'}.pdf"
