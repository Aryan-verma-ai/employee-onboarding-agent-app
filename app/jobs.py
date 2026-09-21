"""Durable per-tenant extraction jobs with compare-and-swap leases."""

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, and_, or_, select, update
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Document, identifier, utcnow


class ExtractionJob(Base):
    __tablename__ = "extraction_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("onboarding_cases.id"))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(default=0)
    available_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)
    lease_until: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    error_code: Mapped[str | None] = mapped_column(String(50))


def enqueue(db, principal, case, document):
    db.scalar(
        select(Document)
        .where(Document.id == document.id, Document.tenant_id == principal.tenant_id)
        .with_for_update()
    )
    job = db.scalar(select(ExtractionJob).where(ExtractionJob.document_id == document.id))
    if job and job.status in {"queued", "running"}:
        return job
    if not job:
        job = ExtractionJob(tenant_id=principal.tenant_id, case_id=case.id, document_id=document.id)
        db.add(job)
    job.status, job.attempts, job.available_at = "queued", 0, utcnow()
    job.lease_until, job.lease_token, job.error_code = None, None, None
    db.flush()
    return job


def claim(db, principal, now=None, lease_seconds=300):
    now = now or utcnow()
    ready = or_(
        and_(ExtractionJob.status == "queued", ExtractionJob.available_at <= now),
        and_(ExtractionJob.status == "running", ExtractionJob.lease_until <= now),
    )
    candidates = db.scalars(
        select(ExtractionJob.id)
        .where(ExtractionJob.tenant_id == principal.tenant_id, ready)
        .order_by(ExtractionJob.available_at)
        .limit(20)
    ).all()
    for job_id in candidates:
        token = str(uuid4())
        changed = db.execute(
            update(ExtractionJob)
            .where(ExtractionJob.id == job_id, ExtractionJob.tenant_id == principal.tenant_id, ready)
            .values(
                status="running",
                lease_token=token,
                lease_until=now + timedelta(seconds=lease_seconds),
                attempts=ExtractionJob.attempts + 1,
            )
        )
        if changed.rowcount:
            db.commit()
            return job_id, token
        db.rollback()
    return None


def fenced_job(db, principal, job_id, token):
    # The conditional write acquires the row lock through the entire result transaction.
    changed = db.execute(
        update(ExtractionJob)
        .where(
            ExtractionJob.id == job_id,
            ExtractionJob.tenant_id == principal.tenant_id,
            ExtractionJob.status == "running",
            ExtractionJob.lease_token == token,
            ExtractionJob.lease_until > utcnow(),
        )
        .values(lease_token=token)
    )
    if not changed.rowcount:
        db.rollback()
        return None
    return db.get(ExtractionJob, job_id)
