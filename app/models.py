from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow():
    return datetime.now(timezone.utc)


def identifier():
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Case(Base):
    __tablename__ = "onboarding_cases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    department: Mapped[str] = mapped_column(String(100))
    consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consent_policy_version: Mapped[str] = mapped_column(String(100), default="1")
    consent_withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validation_outcomes: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default="received")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    missing_fields: Mapped[list] = mapped_column(JSON, default=list)
    conversation_id: Mapped[str | None] = mapped_column(String(200))
    employee_id: Mapped[str | None] = mapped_column(String(50), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    case_id: Mapped[str] = mapped_column(ForeignKey("onboarding_cases.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(100))
    doc_type: Mapped[str] = mapped_column(String(100), default="other")
    version: Mapped[int] = mapped_column(default=1)
    scan_status: Mapped[str] = mapped_column(String(30), default="pending")
    extraction: Mapped[dict] = mapped_column(JSON, default=dict)
    size: Mapped[int] = mapped_column(default=0)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    storage_key: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Employee(Base):
    __tablename__ = "employees"
    __table_args__ = (UniqueConstraint("tenant_id", "pan_fingerprint"),)
    pan_fingerprint: Mapped[str | None] = mapped_column(String(64))
    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("onboarding_cases.id"), unique=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Idempotency(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("tenant_id", "key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    tenant_id: Mapped[str] = mapped_column(String(100))
    key: Mapped[str] = mapped_column(String(128))
    case_id: Mapped[str] = mapped_column(ForeignKey("onboarding_cases.id"))
    employee_id: Mapped[str] = mapped_column(String(50))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("onboarding_cases.id"))
    actor_id: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
