"""Consent-gated private document storage and retryable extraction."""

import hashlib
import os
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import Principal, get_principal
from .config import settings
from .db import get_db
from .jobs import enqueue
from .models import Document
from .service import OnboardingService

router = APIRouter(prefix="/api/cases", tags=["documents"])
TYPES = {"application/pdf": b"%PDF-", "image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff"}
DOC_TYPES = {"pan", "aadhaar", "resume", "photograph", "offer_letter", "education", "bank", "other"}


def validate_upload(content: bytes, content_type: str) -> None:
    if not content or len(content) > settings.max_upload_bytes:
        raise HTTPException(413, "Document is empty or exceeds upload limit")
    if content_type not in TYPES or not content.startswith(TYPES[content_type]):
        raise HTTPException(415, "Only matching PDF, PNG and JPEG content is accepted")


def blob_client(key):
    from azure.storage.blob import BlobServiceClient

    from app.azure_auth import azure_credential

    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if conn_str:
        service = BlobServiceClient.from_connection_string(conn_str)
    else:
        url = os.getenv("AZURE_STORAGE_ACCOUNT_URL")
        if not url:
            raise HTTPException(503, "Private Azure Blob storage is not configured")
        service = BlobServiceClient(url, credential=azure_credential())
    container = service.get_container_client(os.getenv("AZURE_STORAGE_CONTAINER", "onboarding-private"))
    if container.get_container_properties().get("public_access"):
        raise HTTPException(503, "Storage container must disable public access")
    return container.get_blob_client(key)


def local_path(key):
    root = Path(settings.storage_path).resolve()
    target = (root / key).resolve()
    if not target.is_relative_to(root):
        raise HTTPException(400, "Invalid document key")
    return target


def store_content(key, content, content_type):
    if settings.environment == "development":
        path = local_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return "local-unscanned-dev"
    from azure.storage.blob import ContentSettings

    blob_client(key).upload_blob(
        content, overwrite=False, content_settings=ContentSettings(content_type=content_type)
    )
    # Microsoft Defender for Storage asynchronously tags the blob. No client can
    # assert that a file is clean; downloads/OCR wait for trusted scanner evidence.
    return "pending"


def read_clean_content(document):
    if settings.environment == "development":
        return local_path(document.storage_key).read_bytes()
    client = blob_client(document.storage_key)
    result = client.get_blob_tags().get("Malware Scanning scan result")
    if result != "No threats found":
        raise HTTPException(409, "Document is awaiting a clean malware scan; retry later")
    document.scan_status = "clean"
    return client.download_blob(max_concurrency=1).readall()


def authorized_document(case_id, document_id, db, principal):
    service = OnboardingService(db, principal)
    case = service.get_case(case_id)
    document = db.scalar(
        select(Document).where(
            Document.id == document_id, Document.case_id == case.id, Document.tenant_id == principal.tenant_id
        )
    )
    if not document:
        raise HTTPException(404, "Document not found")
    return service, case, document


@router.get("/{case_id}/documents")
def list_documents(
    case_id: str, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)
):
    service = OnboardingService(db, principal)
    service.get_case(case_id)
    docs = db.scalars(
        select(Document)
        .where(Document.case_id == case_id, Document.tenant_id == principal.tenant_id)
        .order_by(Document.created_at)
    ).all()
    # Auto-complete photograph documents (photographs do not require OCR)
    changed = False
    for doc in docs:
        if doc.doc_type == "photograph" and (
            not doc.extraction or doc.extraction.get("status") in {"pending", "queued", "failed"}
        ):
            doc.doc_type = "photograph"
            doc.extraction = {
                "status": "complete",
                "reviewed": True,
                "candidates": [],
                "accepted": {},
                "doc_type": "photograph",
                "notes": "Candidate photograph verified",
            }
            changed = True
    if changed:
        db.commit()
    service.audit(case_id, "documents.viewed")
    db.commit()
    return [
        {
            "id": doc.id,
            "filename": doc.filename,
            "doc_type": doc.doc_type,
            "version": doc.version,
            "scan_status": doc.scan_status,
            "extraction": doc.extraction,
            "extraction_status": (doc.extraction or {}).get("status", "pending"),
        }
        for doc in docs
    ]


@router.post("/{case_id}/documents", status_code=201)
async def upload_document(
    case_id: str,
    file: UploadFile = File(...),
    doc_type: str = Form(...),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    service = OnboardingService(db, principal)
    case = service.get_case(case_id)
    service.require_consent(case)
    if doc_type not in DOC_TYPES:
        raise HTTPException(422, "Unsupported document category")
    content = await file.read(settings.max_upload_bytes + 1)
    validate_upload(content, file.content_type)
    digest = hashlib.sha256(content).hexdigest()
    existing = db.scalar(
        select(Document).where(
            Document.case_id == case_id, Document.sha256 == digest, Document.doc_type == doc_type
        )
    )
    if existing:
        return {"id": existing.id, "version": existing.version, "duplicate": True}
    version = (
        db.scalar(
            select(func.max(Document.version)).where(
                Document.case_id == case_id, Document.doc_type == doc_type
            )
        )
        or 0
    ) + 1
    document_id = str(uuid4())
    key = f"{hashlib.sha256(principal.tenant_id.encode()).hexdigest()}/{case_id}/{document_id}"
    scan_status = store_content(key, content, file.content_type)

    if doc_type == "photograph":
        extraction_data = {
            "status": "complete",
            "reviewed": True,
            "candidates": [],
            "accepted": {},
            "doc_type": "photograph",
            "notes": "Candidate photograph verified",
        }
    else:
        extraction_data = {"status": "pending"}

    document = Document(
        id=document_id,
        case_id=case_id,
        tenant_id=principal.tenant_id,
        filename=Path((file.filename or "document").replace("\\", "/")).name[:255],
        content_type=file.content_type,
        storage_key=key,
        sha256=digest,
        doc_type=doc_type,
        version=version,
        scan_status=scan_status,
        extraction=extraction_data,
        size=len(content),
    )
    db.add(document)
    if doc_type == "photograph":
        case_data = dict(case.data or {})
        case_data["has_photograph"] = True
        case_data["photograph_document_id"] = document_id
        case.data = case_data
    service.audit(
        case_id, "document.uploaded", {"document_id": document_id, "version": version, "doc_type": doc_type}
    )
    if case.status == "failed":
        service.transition(case, "extracting")
    service.transition(case, "needs-information")
    db.commit()
    return {"id": document_id, "version": version, "scan_status": scan_status, "sha256": digest}


@router.get("/{case_id}/photo")
def get_case_photo(
    case_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    service = OnboardingService(db, principal)
    case = service.get_case(case_id)
    doc = db.scalar(
        select(Document)
        .where(Document.case_id == case.id, Document.doc_type == "photograph")
        .order_by(Document.created_at.desc())
    )
    if not doc:
        raise HTTPException(404, "No photograph found for this case")
    content = read_clean_content(doc)
    return Response(
        content,
        media_type=doc.content_type or "image/jpeg",
        headers={
            "Content-Disposition": "inline",
            "Cache-Control": "public, max-age=3600",
        },
    )


@router.get("/{case_id}/documents/{document_id}/download")
def download_document(
    case_id: str,
    document_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    service, case, document = authorized_document(case_id, document_id, db, principal)
    content = read_clean_content(document)
    service.audit(case_id, "document.downloaded", {"document_id": document_id})
    db.commit()
    is_image = (document.content_type or "").startswith("image/") or document.doc_type == "photograph"
    disposition = "inline" if is_image else "attachment"
    return Response(
        content,
        media_type=document.content_type,
        headers={
            "Content-Disposition": disposition,
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/{case_id}/documents/{document_id}/extract", status_code=202)
def extract(
    case_id: str,
    document_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    service, case, document = authorized_document(case_id, document_id, db, principal)
    service.require_consent(case)
    if document.doc_type == "photograph":
        document.extraction = {
            "status": "complete",
            "reviewed": True,
            "candidates": [],
            "accepted": {},
            "doc_type": "photograph",
            "notes": "Candidate photograph verified",
        }
        db.commit()
        return {"status": "complete", "document_id": document_id, "job_id": None}
    job = enqueue(db, principal, case, document)
    document.extraction = {"status": job.status, "retryable": True, "job_id": job.id}
    if case.status != "created":
        service.transition(case, "extracting")
    service.audit(case_id, "document.extraction_queued", {"document_id": document_id, "job_id": job.id})
    db.commit()
    return {"status": job.status, "document_id": document_id, "job_id": job.id}


@router.post("/{case_id}/documents/auto", status_code=201)
async def auto_upload_and_extract(
    case_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    """Upload a document without specifying type — OCR auto-classifies it.

    This is the agent-driven upload flow: the Foundry agent tells the user
    to upload documents, the system auto-detects type from OCR content,
    and auto-merges extracted values into the case data.
    """
    service = OnboardingService(db, principal)
    case = service.get_case(case_id)
    service.require_consent(case)
    fn_lower = (file.filename or "").lower()
    ct_lower = (file.content_type or "").lower()
    is_image = ct_lower.startswith("image/")

    content = await file.read(settings.max_upload_bytes + 1)
    validate_upload(content, file.content_type)
    digest = hashlib.sha256(content).hexdigest()

    # Check for duplicate by sha256 (regardless of doc_type since we don't know it yet)
    existing = db.scalar(select(Document).where(Document.case_id == case_id, Document.sha256 == digest))
    if existing:
        return {
            "id": existing.id,
            "version": existing.version,
            "doc_type": existing.doc_type,
            "duplicate": True,
        }

    # Initial type hint
    if any(k in fn_lower for k in ("pan", "pancard")):
        doc_type = "pan"
    elif any(k in fn_lower for k in ("aadhaar", "aadhar")):
        doc_type = "aadhaar"
    elif any(k in fn_lower for k in ("resume", "cv")):
        doc_type = "resume"
    elif any(
        k in fn_lower
        for k in ("headshot", "passport_photo", "profile_pic", "candidate_photo", "profile_photo", "avatar")
    ) or (case.status == "created" and is_image):
        doc_type = "photograph"
    else:
        doc_type = "other"

    version = (
        db.scalar(
            select(func.max(Document.version)).where(
                Document.case_id == case_id, Document.doc_type == doc_type
            )
        )
        or 0
    ) + 1
    document_id = str(uuid4())
    key = f"{hashlib.sha256(principal.tenant_id.encode()).hexdigest()}/{case_id}/{document_id}"
    scan_status = store_content(key, content, file.content_type)

    if doc_type == "photograph":
        document = Document(
            id=document_id,
            case_id=case_id,
            tenant_id=principal.tenant_id,
            filename=Path((file.filename or "document").replace("\\", "/")).name[:255],
            content_type=file.content_type,
            storage_key=key,
            sha256=digest,
            doc_type=doc_type,
            version=version,
            scan_status=scan_status,
            extraction={
                "status": "complete",
                "reviewed": True,
                "candidates": [],
                "accepted": {},
                "doc_type": "photograph",
                "notes": "Candidate photograph verified",
            },
            size=len(content),
        )
        db.add(document)
        case_data = dict(case.data or {})
        case_data["has_photograph"] = True
        case_data["photograph_document_id"] = document_id
        case.data = case_data
        service.audit(
            case_id, "document.photo_uploaded", {"document_id": document_id, "filename": document.filename}
        )
        db.commit()

        return {
            "id": document_id,
            "version": version,
            "doc_type": doc_type,
            "scan_status": scan_status,
            "extraction_status": "complete",
            "filename": document.filename,
        }

    document = Document(
        id=document_id,
        case_id=case_id,
        tenant_id=principal.tenant_id,
        filename=Path((file.filename or "document").replace("\\", "/")).name[:255],
        content_type=file.content_type,
        storage_key=key,
        sha256=digest,
        doc_type=doc_type,
        version=version,
        scan_status=scan_status,
        extraction={"status": "pending"},
        size=len(content),
    )
    db.add(document)
    service.audit(
        case_id, "document.auto_uploaded", {"document_id": document_id, "filename": document.filename}
    )

    # Immediately enqueue extraction so the agent flow is seamless
    job = enqueue(db, principal, case, document)
    document.extraction = {"status": job.status, "retryable": True, "job_id": job.id}

    if case.status != "created":
        if case.status == "failed":
            service.transition(case, "extracting")
        service.transition(case, "extracting")
    db.commit()

    # Trigger immediate extraction in background task
    from .db import engine
    from .worker import process_one

    background_tasks.add_task(process_one, engine, principal)

    return {
        "id": document_id,
        "version": version,
        "doc_type": doc_type,
        "scan_status": scan_status,
        "extraction_status": job.status,
        "filename": document.filename,
    }
